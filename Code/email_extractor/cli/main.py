#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli/main.py — Точка входа EMAIL EXTRACTOR v12.1 (Memory-Optimized)

Запуск:
    python -m email_extractor.cli.main
    # или
    python Code/email_extractor/cli/main.py

Пять фаз выполняются последовательно:
    0. Локальные файлы (LocalFileScanner)
    1. COMB API (ProxyNova — фоновый запуск)
    2. Pipermail-архивы Ubuntu (PipermailCrawler)
    2.5 HyperKitty-архивы Fedora (HyperKittyCrawler)
    3. GitHub коммиты (GitHubScanner)
    4. Google Dorks (DorkScanner)

Оптимизация памяти v12.1:
    - checkpoint["processed"] перенесён из RAM в SQLite (таблица processed_urls)
    - MAX_CONCURRENT снижен до 20 (было 100)
    - MAX_MB снижен до 5 (было 20)
    - BS4 объекты явно освобождаются через decompose()
    - MxChecker использует LRU-кэш с лимитом 5000 доменов
"""
from __future__ import annotations

import asyncio
import gc
import logging
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Подавление шумных логгеров сторонних библиотек
# ---------------------------------------------------------------------------
import logging as _lg

_lg.getLogger("fake_useragent").setLevel(_lg.ERROR)
_lg.getLogger("httpx").setLevel(_lg.WARNING)
_lg.getLogger("dns").setLevel(_lg.WARNING)

# ---------------------------------------------------------------------------
# tqdm — мягкий импорт
# ---------------------------------------------------------------------------
try:
    from tqdm.asyncio import tqdm as async_tqdm  # type: ignore[import]
    from tqdm import tqdm as sync_tqdm  # type: ignore[import]

    TQDM_OK = True
except ImportError:
    TQDM_OK = False

    class _FakeTqdm:
        """Заглушка tqdm для случая, когда библиотека не установлена."""

        def __init__(self, iterable=None, desc=None, total=None, **kwargs):
            self.iterable = iterable
            self.desc = desc or ""
            self.total = total
            self.n = 0

        def update(self, n: int = 1) -> None:
            self.n += n
            if self.total:
                print(f"\r{self.desc}: {self.n}/{self.total}", end="", flush=True)

        def close(self) -> None:
            print()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

        def __aiter__(self):
            return iter(self.iterable or [])

    async_tqdm = _FakeTqdm  # type: ignore[assignment]
    sync_tqdm = _FakeTqdm  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Импорты внутри пакета
# ---------------------------------------------------------------------------
# Гарантируем, что корень workspace в sys.path (для запуска как скрипта)
_ROOT = Path(__file__).resolve().parent.parent.parent  # Code/
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import (  # noqa: E402
    CHECKPOINT_FILE,
    CSV_OUTPUT,
    GITHUB_TOKEN,
    COMB_API_URL,
    COMB_DOMAINS,
    COMB_SLEEP,
    EMAIL_DORKS,
    DORK_RESULTS_PER_QUERY,
    DORK_SLEEP,
    HYPERKITTY_SERVERS,
    LOCAL_SCAN_DIR,
    LOG_DATE_FORMAT,
    LOG_FORMAT,
    LOG_LEVEL,
    MAX_CONCURRENT,
    MAX_MB,
    MEMORY_LIMIT_MB,
    PARSER_SOURCES,
    PIPERMAIL_SERVERS,
    REQUEST_TIMEOUT,
    TXT_OUTPUT,
    DB_OUTPUT,
    BACKEND_INDEX,
    BACKEND_TOTAL,
)

def _source_enabled(name: str) -> bool:
    """Проверяет, включён ли данный источник на этом инстансе."""
    return "all" in PARSER_SOURCES or name in PARSER_SOURCES
from email_extractor.core.entities import Contact
from email_extractor.infrastructure.sqlite_repository import SqliteContactRepository
from email_extractor.infrastructure.http_client import AsyncHttpClient
from email_extractor.services.email_extractor import EmailExtractorService, is_fake_email
from email_extractor.services.github_scanner import GitHubScanner
from email_extractor.services.local_file_scanner import LocalFileScanner
from email_extractor.services.mx_checker import MxChecker
from email_extractor.services.pipermail_crawler import PipermailCrawler
from email_extractor.services.comb_scanner import CombApiScanner
from email_extractor.services.dork_scanner import DorkScanner
from email_extractor.services.hyperkitty_crawler import HyperKittyCrawler
from websocket_manager import ws_manager

# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

log = logging.getLogger(__name__)


def _format_time(seconds: float) -> str:
    """Форматировать секунды в читаемую строку (Nч Nм Nс)."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}ч {m}м {s}с"
    if m > 0:
        return f"{m}м {s}с"
    return f"{s}с"


def _touch_checkpoint() -> None:
    """Создать пустой файл-маркер checkpoint.json (защита от очистки БД при рестарте)."""
    try:
        CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
        CHECKPOINT_FILE.touch(exist_ok=True)
    except Exception as exc:
        log.warning("⚠ Не удалось создать checkpoint-маркер: %s", exc)


def _get_memory_mb() -> float:
    """Получить текущее потребление RAM процессом (MB). Кроссплатформенно."""
    try:
        import resource
        # Linux/Mac: ru_maxrss в KB
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return usage.ru_maxrss / 1024  # KB -> MB
    except ImportError:
        pass
    try:
        # Fallback: читаем /proc/self/status (Linux / Railway)
        with open("/proc/self/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024  # KB -> MB
    except Exception:
        pass
    return 0.0


# ---------------------------------------------------------------------------
# Обработка одного URL
# ---------------------------------------------------------------------------

async def _process_url(
    http: AsyncHttpClient,
    url: str,
    sem: asyncio.Semaphore,
    extractor: EmailExtractorService,
    mx: MxChecker,
    repo: SqliteContactRepository,
    db_contact_queue: asyncio.Queue,
    db_url_queue: asyncio.Queue,
    pbar=None,
) -> int:
    """Скачать URL потоково, извлечь контакты, добавить в очередь записи БД."""
    async with sem:
        if _STOP_REQUESTED or _CANCEL_REQUESTED:
            return 0

        if repo.is_url_processed(url):
            return 0

        # Мгновенная пометка в LRU-кэше для других воркеров
        repo._cache_add(url)
        await db_url_queue.put(url)

        stream = http.stream_lines(url)
        found = await extractor.extract_from_stream(stream, url)
        
        added = 0
        for contact in found:
            e = contact.email
            if not e or is_fake_email(e):
                continue
            if not await mx.check(contact.domain):
                continue
            await db_contact_queue.put(contact)
            added += 1

        if added > 0:
            msg = f"   🔎 Сканирую {url[:60]} — нашёл {len(found)} адресов, {len(found) - added} фейковых отсеял. Остаток ({added} шт.) отправил в базу на проверку дубликатов..."
            log.info(msg)
            asyncio.create_task(ws_manager.send_log(msg))

        if pbar:
            pbar.update(1)
        return added

async def _worker(
    queue: asyncio.Queue,
    db_contact_queue: asyncio.Queue,
    db_url_queue: asyncio.Queue,
    http: AsyncHttpClient,
    sem: asyncio.Semaphore,
    extractor: EmailExtractorService,
    mx: MxChecker,
    repo: SqliteContactRepository,
    pbar,
) -> None:
    """Воркер для параллельной обработки URL (без состояния в RAM)"""
    while True:
        url = await queue.get()
        try:
            if not (_STOP_REQUESTED or _CANCEL_REQUESTED):
                await _process_url(http, url, sem, extractor, mx, repo, db_contact_queue, db_url_queue, pbar)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"Worker failed on {url}: {e}")
        finally:
            queue.task_done()


async def _db_writer(
    db_contact_queue: asyncio.Queue,
    db_url_queue: asyncio.Queue,
    repo: SqliteContactRepository,
) -> None:
    """Batch Writer (для SQLite) - пишет данные пачками для оптимизации"""
    gc_counter = 0
    while True:
        try:
            if _STOP_REQUESTED or _CANCEL_REQUESTED:
                break
            contacts_to_add = []
            urls_to_mark = []
            # Берем из очереди пакетами, чтобы не заблокировать writer
            for _ in range(5000):
                try:
                    urls_to_mark.append(db_url_queue.get_nowait())
                    db_url_queue.task_done()
                except asyncio.QueueEmpty:
                    break
            
            for _ in range(5000):
                try:
                    contacts_to_add.append(db_contact_queue.get_nowait())
                    db_contact_queue.task_done()
                except asyncio.QueueEmpty:
                    break

            if urls_to_mark:
                repo.mark_urls_processed_bulk(urls_to_mark)
            if contacts_to_add:
                added = repo.add_contacts_bulk(contacts_to_add)
                duplicates = len(contacts_to_add) - added
                if added > 0 or duplicates > 0:
                    total = repo.get_count()
                    msg_db = f"   ✅💾 ИТОГ ПРОВЕРКИ: Прилетело {len(contacts_to_add)} → {duplicates} ДУБЛИКАТОВ (выброшены) | +{added} НОВЫХ УНИКАЛЬНЫХ | Всего в базе: {total}"
                    log.info(msg_db)
                    asyncio.create_task(ws_manager.send_log(msg_db))
                
                if added > 0:
                    asyncio.create_task(ws_manager.send_count(repo.get_count()))

            # Периодический GC + мониторинг памяти (каждые ~12 сек)
            gc_counter += 1
            if gc_counter >= 60:  # 60 * 0.2s = 12 сек
                gc_counter = 0
                gc.collect()
                repo.truncate_wal()  # Очищаем WAL-файл, чтобы Railway не считал его за оперативную память
                mem_mb = _get_memory_mb()
                if mem_mb > 0:
                    if mem_mb > MEMORY_LIMIT_MB:
                        msg = f"⚠️ ПАМЯТЬ: {mem_mb:.0f}MB > {MEMORY_LIMIT_MB}MB — торможу на 10с + GC..."
                        log.warning(msg)
                        asyncio.create_task(ws_manager.send_log(msg))
                        gc.collect()
                        await asyncio.sleep(10)
                        gc.collect()
                        repo.truncate_wal()

            await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"DB Writer error: {e}")
            await asyncio.sleep(1)

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

_IS_RUNNING = False
_STOP_REQUESTED = False
_CANCEL_REQUESTED = False
_START_TIME = None
_CURRENT_TASK = None


async def main() -> None:
    global _IS_RUNNING, _STOP_REQUESTED, _CANCEL_REQUESTED, _START_TIME, _CURRENT_TASK
    if _IS_RUNNING:
        log.warning("⚠ Экстракция уже запущена. Повторный вызов пропущен.")
        return
    _IS_RUNNING = True
    _STOP_REQUESTED = False
    _CANCEL_REQUESTED = False
    _CURRENT_TASK = asyncio.current_task()
    try:
        await _main_logic()
    except asyncio.CancelledError:
        log.warning("🛑 ЗАДАЧА БЫЛА ЖЕСТКО ОТМЕНЕНА/ОСТАНОВЛЕНА")
        if _CANCEL_REQUESTED:
            ws_manager.clear_history()
            ws_manager.email_count = 0
            asyncio.create_task(ws_manager.send_count(0))
            # Удаляем маркер и очищаем БД полностью
            if CHECKPOINT_FILE.exists():
                CHECKPOINT_FILE.unlink()
            repo = SqliteContactRepository(db_path=DB_OUTPUT)
            repo.clear_all()
            repo.clear_processed_urls()
        elif _STOP_REQUESTED:
            # При паузе — данные сохранены в SQLite, маркер оставляем
            pass
    except Exception as exc:
        log.error("💥 Ошибка парсинга: %s", exc, exc_info=True)
        raise  # Пробрасываем ошибку выше, чтобы engine.py мог поймать ее и запустить авто-возобновление
    finally:
        _IS_RUNNING = False
        _START_TIME = None
        _CURRENT_TASK = None


async def _main_logic() -> None:
    global _STOP_REQUESTED, _CANCEL_REQUESTED, _START_TIME
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT,
    )

    # ------------------------------------------------------------------
    # Worker function extracted to module scope for better decomposition
    # ------------------------------------------------------------------

    _START_TIME = datetime.now().timestamp()
    start_time = datetime.now()

    ws_manager.clear_history()

    msg_start = "🚀 ЗАПУСК EMAIL EXTRACTOR v12.1 — MEMORY OPTIMIZED"
    log.info("=" * 60)
    log.info(msg_start)
    log.info("=" * 60)
    asyncio.create_task(ws_manager.send_log("=" * 60))
    asyncio.create_task(ws_manager.send_log(msg_start))
    asyncio.create_task(ws_manager.send_log("=" * 60))

    # Лог распределения работы
    bi_label = f"#{BACKEND_INDEX}" if BACKEND_INDEX is not None else "единственный"
    msg_dist = (
        f"📡 Бэкенд {bi_label} | Источники: {','.join(sorted(PARSER_SOURCES))} | "
        f"Потоков: {MAX_CONCURRENT} | "
        f"Pipermail: {len(PIPERMAIL_SERVERS)} серв. | "
        f"COMB: {len(COMB_DOMAINS)} дом. | "
        f"Дорков: {len(EMAIL_DORKS)}"
    )
    log.info(msg_dist)
    asyncio.create_task(ws_manager.send_log(msg_dist))

    # ------------------------------------------------------------------
    # Инициализация компонентов
    # ------------------------------------------------------------------
    repo = SqliteContactRepository(db_path=DB_OUTPUT)

    # Если маркер-файл отсутствует → новый запуск → очищаем базу полностью.
    # Если маркер есть → продолжение после паузы/краша → данные сохраняем.
    if not CHECKPOINT_FILE.exists():
        repo.clear_all()
        repo.clear_processed_urls()
        ws_manager.email_count = 0
    else:
        # Восстанавливаем счетчик, так как clear_history сбросил его в 0 перед стартом
        count = repo.get_count()
        ws_manager.email_count = count
        asyncio.create_task(ws_manager.send_count(count))

    # ------------------------------------------------------------------
    # Batch Writer (для SQLite)
    # Ограничиваем размер очередей для предотвращения утечек памяти (Backpressure)
    # ------------------------------------------------------------------
    db_contact_queue: asyncio.Queue = asyncio.Queue(maxsize=10000)
    db_url_queue: asyncio.Queue = asyncio.Queue(maxsize=10000)

    db_writer_task = asyncio.create_task(_db_writer(db_contact_queue, db_url_queue, repo))
    comb_task = None
    workers = []

    try:

        mx = MxChecker()
        extractor = EmailExtractorService()
        sem = asyncio.Semaphore(MAX_CONCURRENT)

        # Создаём маркер-файл СРАЗУ — защита от очистки БД при краше/OOM
        _touch_checkpoint()

        processed_before = repo.get_processed_count()
        log.info("📌 Уже обработано URL (из прошлых запусков): %d", processed_before)

        # ------------------------------------------------------------------
        # ФАЗА 0: Локальные файлы
        # ------------------------------------------------------------------
        log.info("\n📂 ЭТАП 0: Локальное сканирование")
        phase_start = datetime.now()

        scanner = LocalFileScanner(extensions={"*"})
        local_contacts: list[Contact] = await asyncio.to_thread(scanner.scan, LOCAL_SCAN_DIR)

        with sync_tqdm(local_contacts, desc="📂 Фильтрация локальных", unit="шт") as pbar:
            added_local = 0
            for c in pbar:
                if _STOP_REQUESTED or _CANCEL_REQUESTED:
                    break
                if not is_fake_email(c.email):
                    if repo.add_if_not_exists(c):
                        added_local += 1

        if added_local > 0:
            asyncio.create_task(ws_manager.send_count(repo.get_count()))

        msg_local = f"✅ Локальные файлы добавили {added_local} адресов. Этап: {_format_time((datetime.now() - phase_start).total_seconds())}"
        log.info(msg_local)
        asyncio.create_task(ws_manager.send_log(msg_local))

        # ------------------------------------------------------------------
        # Открываем HTTP-клиент для сетевых фаз
        # ------------------------------------------------------------------
        async with AsyncHttpClient(
            timeout=REQUEST_TIMEOUT,
            max_mb=MAX_MB,
            max_connections=MAX_CONCURRENT * 2,
            max_keepalive=MAX_CONCURRENT,
        ) as http:

            # --------------------------------------------------------------
            # Пул воркеров (повторно используется для всех сетевых фаз)
            # --------------------------------------------------------------
            url_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=200)

            def _spawn_workers(pbar=None):
                nonlocal workers
                for w in workers:
                    w.cancel()
                workers = [
                    asyncio.create_task(
                        _worker(url_queue, db_contact_queue, db_url_queue, http, sem, extractor, mx, repo, pbar)
                    )
                    for _ in range(MAX_CONCURRENT)
                ]

            # --------------------------------------------------------------
            # ФАЗА 1: COMB API (ProxyNova) - Запускаем в фоне!
            # --------------------------------------------------------------
            if _source_enabled("comb") and not (_STOP_REQUESTED or _CANCEL_REQUESTED):
                log.info("\n🔓 ЭТАП 1: COMB API (ProxyNova — публичная база утечек) - Запуск в фоне")
                asyncio.create_task(ws_manager.send_log("🔓 ЭТАП 1: COMB API запущен в фоновом режиме..."))
            
                async def _run_comb():
                    phase_start = datetime.now()
                    comb = CombApiScanner(
                        api_url=COMB_API_URL,
                        domains=COMB_DOMAINS,
                        sleep_between=COMB_SLEEP,
                    )
                    comb_contacts = await comb.scan(http)
                    added_comb = 0
                    for contact in comb_contacts:
                        if _STOP_REQUESTED or _CANCEL_REQUESTED:
                            break
                        if await mx.check(contact.domain):
                            await db_contact_queue.put(contact)
                            added_comb += 1
                    msg_comb = f"✅ COMB API: +{added_comb} адресов из {len(COMB_DOMAINS)} доменов. Завершено за: {_format_time((datetime.now() - phase_start).total_seconds())}"
                    log.info(msg_comb)
                    asyncio.create_task(ws_manager.send_log(msg_comb))

                comb_task = asyncio.create_task(_run_comb())



            # --------------------------------------------------------------
            # ФАЗА 2: Pipermail
            # --------------------------------------------------------------
            if _source_enabled("pipermail") and not (_STOP_REQUESTED or _CANCEL_REQUESTED):
                log.info("\n📧 ЭТАП 2: Архивы Pipermail")
                phase_start = datetime.now()
                crawler = PipermailCrawler(servers=PIPERMAIL_SERVERS)
                pbar = async_tqdm(desc="Обработка страниц", unit="стр", position=0, total=None)
                _spawn_workers(pbar)

                discovered_count = 0
                async for url in crawler.discover(http):
                    if _STOP_REQUESTED or _CANCEL_REQUESTED:
                        break
                    if not repo.is_url_processed(url):
                        await url_queue.put(url)
                        discovered_count += 1

                await url_queue.join()
                pbar.close()

                msg_piper = f"✅ Обработано {discovered_count} страниц Pipermail. Этап: {_format_time((datetime.now() - phase_start).total_seconds())}"
                log.info(msg_piper)
                asyncio.create_task(ws_manager.send_log(msg_piper))

            # --------------------------------------------------------------
            # ФАЗА 2.5: HyperKitty (Fedora)
            # --------------------------------------------------------------
            if _source_enabled("hyperkitty") and not (_STOP_REQUESTED or _CANCEL_REQUESTED) and HYPERKITTY_SERVERS:
                log.info("\n📧 ЭТАП 2.5: HyperKitty (Fedora)")
                phase_start = datetime.now()
                asyncio.create_task(ws_manager.send_log("📧 ЭТАП 2.5: HyperKitty (Fedora) — обход архивов..."))

                hk_crawler = HyperKittyCrawler(servers=HYPERKITTY_SERVERS)
                pbar_hk = async_tqdm(desc="HyperKitty страницы", unit="стр", position=0, total=None)
                _spawn_workers(pbar_hk)

                hk_discovered = 0
                async for url in hk_crawler.discover(http):
                    if _STOP_REQUESTED or _CANCEL_REQUESTED:
                        break
                    if not repo.is_url_processed(url):
                        await url_queue.put(url)
                        hk_discovered += 1

                await url_queue.join()
                pbar_hk.close()

                msg_hk = f"✅ HyperKitty (Fedora): обработано {hk_discovered} страниц. Этап: {_format_time((datetime.now() - phase_start).total_seconds())}"
                log.info(msg_hk)
                asyncio.create_task(ws_manager.send_log(msg_hk))

            # --------------------------------------------------------------
            # ФАЗА 3: GitHub
            # --------------------------------------------------------------
            if _source_enabled("github") and not (_STOP_REQUESTED or _CANCEL_REQUESTED):
                log.info("\n🐙 ЭТАП 3: GitHub")
                phase_start = datetime.now()

                github = GitHubScanner(token=GITHUB_TOKEN)
                gh_contacts = await github.scan(http)
                added_gh = 0

                for contact in gh_contacts:
                    if _STOP_REQUESTED or _CANCEL_REQUESTED:
                        break
                    if await mx.check(contact.domain):
                        await db_contact_queue.put(contact)
                        added_gh += 1

                if added_gh > 0:
                    msg_gh = f"   🎯 Процессинг GitHub: +{added_gh} адресов"
                    log.info(msg_gh)
                    asyncio.create_task(ws_manager.send_log(msg_gh))

            # --------------------------------------------------------------
            # ФАЗА 4: Google Dorks
            # --------------------------------------------------------------
            if _source_enabled("dorks") and not (_STOP_REQUESTED or _CANCEL_REQUESTED) and EMAIL_DORKS:
                log.info("\n🔍 ЭТАП 4: Google Dorks (%d запросов)", len(EMAIL_DORKS))
                phase_start = datetime.now()
                asyncio.create_task(ws_manager.send_log(f"🔍 ЭТАП 4: Google Dorks — {len(EMAIL_DORKS)} поисковых запросов..."))

                dork_scanner = DorkScanner(
                    dorks=EMAIL_DORKS,
                    results_per_query=DORK_RESULTS_PER_QUERY,
                    sleep_between=DORK_SLEEP,
                )

                pbar_dork = async_tqdm(desc="Обработка Dork-URL", unit="стр", position=0, total=None)
                _spawn_workers(pbar_dork)

                dork_discovered = 0
                async for url in dork_scanner.discover(http, known_urls=set()):
                    if _STOP_REQUESTED or _CANCEL_REQUESTED:
                        break
                    if not repo.is_url_processed(url):
                        await url_queue.put(url)
                        dork_discovered += 1

                await url_queue.join()
                pbar_dork.close()

                msg_dork = f"✅ Google Dorks: обработано {dork_discovered} URL. Этап: {_format_time((datetime.now() - phase_start).total_seconds())}"
                log.info(msg_dork)
                asyncio.create_task(ws_manager.send_log(msg_dork))

            # Ждём завершения фонового COMB API
            if comb_task:
                await comb_task

            total_elapsed = (datetime.now() - start_time).total_seconds()
            total_emails = repo.get_count()
            total_processed = repo.get_processed_count()

            msg_end = (
                f"{'=' * 60}\n"
                f"🏁 РАБОТА ЗАВЕРШЕНА за {_format_time(total_elapsed)}\n"
                f"📊 ВСЕГО УНИКАЛЬНЫХ EMAIL: {total_emails}\n"
                f"🔗 Обработано URL: {total_processed}\n"
                f"💾 MX-кэш: {mx.cache_size} доменов проверено\n"
                f"{'=' * 60}"
            )
            log.info(msg_end)
            asyncio.create_task(ws_manager.send_log(msg_end))

    finally:
        # Гарантированное завершение всех фоновых задач
        db_writer_task.cancel()
        if comb_task and not comb_task.done():
            comb_task.cancel()
        for w in workers:
            if not w.done():
                w.cancel()

        # Финальный сброс очередей в базу
        contacts_to_add = []
        urls_to_mark = []
        while not db_url_queue.empty():
            urls_to_mark.append(db_url_queue.get_nowait())
        while not db_contact_queue.empty():
            contacts_to_add.append(db_contact_queue.get_nowait())
        if urls_to_mark:
            repo.mark_urls_processed_bulk(urls_to_mark)
        if contacts_to_add:
            repo.add_contacts_bulk(contacts_to_add)

        if _CANCEL_REQUESTED:
            ws_manager.clear_history()
            ws_manager.email_count = 0
            asyncio.create_task(ws_manager.send_count(0))

            msg_cancel = "🛑 СЕССИЯ ОТМЕНЕНА. Прогресс полностью стёрт. Готов к новому старту."
            log.warning(msg_cancel)
            asyncio.create_task(ws_manager.send_log(msg_cancel))

            # Удаляем маркер — следующий старт начнёт с чистого листа
            if CHECKPOINT_FILE.exists():
                CHECKPOINT_FILE.unlink()
        # else: маркер остаётся — следующий запуск продолжит с того же места


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("🛑 Прервано пользователем.")
        sys.exit(0)
