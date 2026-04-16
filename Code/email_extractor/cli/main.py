#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli/main.py — Точка входа EMAIL EXTRACTOR v12.1 (Memory-Optimized)

Запуск:
    python -m email_extractor.cli.main
    # или
    python Code/email_extractor/cli/main.py

Четыре фазы выполняются последовательно:
    0. Локальные файлы (LocalFileScanner)
    1. Pipermail-архивы (PipermailCrawler)
    2. Google Dorks (GoogleDorksDiscovery)
    3. GitHub коммиты (GitHubScanner)

Оптимизация памяти v12.1:
    - checkpoint["processed"] перенесён из RAM в SQLite (таблица processed_urls)
    - MAX_CONCURRENT снижен до 20 (было 100)
    - MAX_MB снижен до 5 (было 20)
    - BS4 объекты явно освобождаются через decompose()
    - MxChecker использует LRU-кэш с лимитом 5000 доменов
"""
from __future__ import annotations

import asyncio
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
    DORK_SLEEP,
    DORK_RESULTS_PER_QUERY,
    EMAIL_DORKS,
    GITHUB_TOKEN,
    LOCAL_SCAN_DIR,
    LOG_DATE_FORMAT,
    LOG_FORMAT,
    LOG_LEVEL,
    MAX_CONCURRENT,
    MAX_MB,
    PIPERMAIL_SERVERS,
    REQUEST_TIMEOUT,
    TXT_OUTPUT,
    DB_OUTPUT,
)
from email_extractor.core.entities import Contact
from email_extractor.infrastructure.sqlite_repository import SqliteContactRepository
from email_extractor.infrastructure.http_client import AsyncHttpClient
from email_extractor.services.email_extractor import EmailExtractorService, is_fake_email
from email_extractor.services.github_scanner import GitHubScanner
from email_extractor.services.google_dorks import GoogleDorksDiscovery
from email_extractor.services.local_file_scanner import LocalFileScanner
from email_extractor.services.mx_checker import MxChecker
from email_extractor.services.pipermail_crawler import PipermailCrawler
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
    pbar=None,
) -> int:
    """Скачать URL, извлечь контакты, проверить MX, добавить в БД."""
    async with sem:
        if _STOP_REQUESTED or _CANCEL_REQUESTED:
            return 0

        if repo.is_url_processed(url):
            return 0

        raw = await http.fetch(url)
        # Сразу помечаем как обработанный — даже если контента нет
        repo.mark_url_processed(url)

        if not raw:
            return 0

        found = extractor.extract_from_url_content(raw, url)
        raw = None  # освобождаем буфер ответа немедленно
        added = 0

        for contact in found:
            e = contact.email
            if not e or is_fake_email(e):
                continue
            if not await mx.check(contact.domain):
                continue
            if repo.add_if_not_exists(contact):
                added += 1

        if added:
            msg = f"   🎯 +{added} адресов: {url[:70]}"
            log.info(msg)
            asyncio.create_task(ws_manager.send_log(msg))
            asyncio.create_task(ws_manager.send_count(repo.get_count()))

        if pbar:
            pbar.update(1)
        return added


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
    # Worker для параллельной обработки URL (без состояния в RAM)
    # ------------------------------------------------------------------
    async def _worker(
        queue: asyncio.Queue,
        http: AsyncHttpClient,
        sem: asyncio.Semaphore,
        extractor: EmailExtractorService,
        mx: MxChecker,
        repo: SqliteContactRepository,
        pbar,
    ) -> None:
        while True:
            url = await queue.get()
            try:
                if not (_STOP_REQUESTED or _CANCEL_REQUESTED):
                    await _process_url(http, url, sem, extractor, mx, repo, pbar)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Worker failed on {url}: {e}")
            finally:
                queue.task_done()

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
        ws_manager.email_count = repo.get_count()

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
        url_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=500)
        workers = []

        def _spawn_workers(pbar=None):
            nonlocal workers
            for w in workers:
                w.cancel()
            workers = [
                asyncio.create_task(
                    _worker(url_queue, http, sem, extractor, mx, repo, pbar)
                )
                for _ in range(MAX_CONCURRENT)
            ]

        # --------------------------------------------------------------
        # ФАЗА 1: Pipermail
        # --------------------------------------------------------------
        log.info("\n📧 ЭТАП 1: Архивы Pipermail")
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
        # ФАЗА 2: Google Dorks
        # --------------------------------------------------------------
        log.info("\n🔍 ЭТАП 2: Google Dorks")
        phase_start = datetime.now()

        discovery = GoogleDorksDiscovery(
            dorks=EMAIL_DORKS,
            results_per_query=DORK_RESULTS_PER_QUERY,
            sleep_between=DORK_SLEEP,
        )
        # Передаём пустой set — фильтрация дублей теперь в SQLite
        dork_urls = await discovery.discover(http, set())
        dork_urls = [u for u in dork_urls if not repo.is_url_processed(u)]

        pbar = async_tqdm(total=len(dork_urls), desc="Обработка Dorks", unit="URL", position=0)
        _spawn_workers(pbar)

        for url in dork_urls:
            if _STOP_REQUESTED or _CANCEL_REQUESTED:
                break
            await url_queue.put(url)

        await url_queue.join()
        pbar.close()

        msg_dork = f"✅ Обработано {len(dork_urls)} URL из Dorks. Этап: {_format_time((datetime.now() - phase_start).total_seconds())}"
        log.info(msg_dork)
        asyncio.create_task(ws_manager.send_log(msg_dork))

        # Останавливаем воркеров
        for w in workers:
            w.cancel()
        workers = []

        # --------------------------------------------------------------
        # ФАЗА 3: GitHub
        # --------------------------------------------------------------
        log.info("\n🐙 ЭТАП 3: GitHub")
        phase_start = datetime.now()

        github = GitHubScanner(token=GITHUB_TOKEN)
        gh_contacts = await github.scan(http)
        added_gh = 0

        for contact in gh_contacts:
            if _STOP_REQUESTED or _CANCEL_REQUESTED:
                break
            if await mx.check(contact.domain):
                if repo.add_if_not_exists(contact):
                    added_gh += 1

        if added_gh > 0:
            asyncio.create_task(ws_manager.send_count(repo.get_count()))

        msg_gh = f"✅ GitHub добавил {added_gh} адресов. Этап: {_format_time((datetime.now() - phase_start).total_seconds())}"
        log.info(msg_gh)
        asyncio.create_task(ws_manager.send_log(msg_gh))

    # ------------------------------------------------------------------
    # Завершение
    # ------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("🛑 Прервано пользователем.")
        sys.exit(0)
