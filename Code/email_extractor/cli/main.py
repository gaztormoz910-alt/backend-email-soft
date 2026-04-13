#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli/main.py — Точка входа EMAIL EXTRACTOR v12.0 FINAL

Запуск:
    python -m email_extractor.cli.main
    # или
    python Code/email_extractor/cli/main.py

Четыре фазы выполняются последовательно:
    0. Локальные файлы (LocalFileScanner)
    1. Pipermail-архивы (PipermailCrawler)
    2. Google Dorks (GoogleDorksDiscovery)
    3. GitHub коммиты (GitHubScanner)
"""
from __future__ import annotations

import asyncio
import json
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
)
from email_extractor.core.entities import Contact
from email_extractor.infrastructure.csv_writer import CsvContactRepository
from email_extractor.infrastructure.http_client import AsyncHttpClient
from email_extractor.services.email_extractor import EmailExtractorService, is_fake_email
from email_extractor.services.github_scanner import GitHubScanner
from email_extractor.services.google_dorks import GoogleDorksDiscovery
from email_extractor.services.local_file_scanner import LocalFileScanner
from email_extractor.services.mx_checker import MxChecker
from email_extractor.services.pipermail_crawler import PipermailCrawler

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


def _load_checkpoint() -> dict:
    if CHECKPOINT_FILE.exists():
        try:
            with open(CHECKPOINT_FILE, encoding="utf-8") as f:
                data = json.load(f)
            log.info("⏯ Продолжаем с контрольной точки. Обработано: %d URL", len(data.get("processed", [])))
            return data
        except Exception:
            pass
    return {"processed": []}


def _save_checkpoint(checkpoint: dict) -> None:
    try:
        with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
            json.dump(checkpoint, f)
    except Exception as exc:
        log.warning("⚠ Не удалось сохранить checkpoint: %s", exc)


# ---------------------------------------------------------------------------
# Обработка одного URL
# ---------------------------------------------------------------------------

async def _process_url(
    http: AsyncHttpClient,
    url: str,
    sem: asyncio.Semaphore,
    extractor: EmailExtractorService,
    mx: MxChecker,
    contacts: dict[str, Contact],
    checkpoint: dict,
    pbar=None,
) -> int:
    """Скачать URL, извлечь контакты, проверить MX, добавить в словарь."""
    async with sem:
        if url in checkpoint["processed"]:
            return 0

        raw = await http.fetch(url)
        if not raw:
            checkpoint["processed"].append(url)
            return 0

        found = extractor.extract_from_url_content(raw, url)
        added = 0

        for contact in found:
            e = contact.email
            if not e or is_fake_email(e):
                continue
            if not await mx.check(contact.domain):
                continue
            if e not in contacts:
                contacts[e] = contact
                added += 1

        if added:
            log.info("   🎯 +%d адресов: %s", added, url[:70])

        checkpoint["processed"].append(url)
        if pbar:
            pbar.update(1)
        return added


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

async def main() -> None:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT,
    )
    start_time = datetime.now()

    log.info("=" * 60)
    log.info("🚀 ЗАПУСК EMAIL EXTRACTOR v12.0 FINAL — MAXIMUM OVERDRIVE")
    log.info("=" * 60)

    # ------------------------------------------------------------------
    # Инициализация компонентов
    # ------------------------------------------------------------------
    repo = CsvContactRepository(csv_path=CSV_OUTPUT, txt_path=TXT_OUTPUT)
    mx = MxChecker()
    extractor = EmailExtractorService()
    sem = asyncio.Semaphore(MAX_CONCURRENT)

    # ------------------------------------------------------------------
    # Загрузка существующих контактов + checkpoint
    # ------------------------------------------------------------------
    contacts: dict[str, Contact] = repo.load()
    checkpoint = _load_checkpoint()

    # ------------------------------------------------------------------
    # ФАЗА 0: Локальные файлы
    # ------------------------------------------------------------------
    log.info("\n📂 ЭТАП 0: Локальное сканирование")
    phase_start = datetime.now()
    before = len(contacts)

    scanner = LocalFileScanner(extensions={"*"})
    local_contacts: list[Contact] = await asyncio.to_thread(scanner.scan, LOCAL_SCAN_DIR)

    with sync_tqdm(local_contacts, desc="📂 Фильтрация локальных", unit="шт") as pbar:
        for c in pbar:
            if c.email not in contacts and not is_fake_email(c.email):
                contacts[c.email] = c

    log.info(
        "✅ Локальные файлы добавили %d адресов. Этап: %s",
        len(contacts) - before,
        _format_time((datetime.now() - phase_start).total_seconds()),
    )

    # ------------------------------------------------------------------
    # Открываем HTTP-клиент для сетевых фаз
    # ------------------------------------------------------------------
    async with AsyncHttpClient(
        timeout=REQUEST_TIMEOUT,
        max_mb=MAX_MB,
        max_connections=200,
        max_keepalive=50,
    ) as http:

        # --------------------------------------------------------------
        # ФАЗА 1: Pipermail
        # --------------------------------------------------------------
        log.info("\n📧 ЭТАП 1: Архивы Pipermail")
        phase_start = datetime.now()
        pipermail_tasks: list[asyncio.Task] = []
        crawler = PipermailCrawler(servers=PIPERMAIL_SERVERS)

        pbar = async_tqdm(desc="Обработка страниц", unit="стр", position=0, total=None)
        async for url in crawler.discover(http):
            if url not in checkpoint["processed"]:
                task = asyncio.create_task(
                    _process_url(http, url, sem, extractor, mx, contacts, checkpoint, pbar)
                )
                pipermail_tasks.append(task)

        if pipermail_tasks:
            await asyncio.gather(*pipermail_tasks)
        pbar.close()

        log.info(
            "✅ Обработано %d страниц Pipermail. Этап: %s",
            len(pipermail_tasks),
            _format_time((datetime.now() - phase_start).total_seconds()),
        )

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
        dork_urls = await discovery.discover(http, set(checkpoint["processed"]))
        dork_urls = [u for u in dork_urls if u not in checkpoint["processed"]]

        pbar = async_tqdm(total=len(dork_urls), desc="Обработка Dorks", unit="URL", position=0)
        dork_tasks = [
            _process_url(http, url, sem, extractor, mx, contacts, checkpoint, pbar)
            for url in dork_urls
        ]
        await asyncio.gather(*dork_tasks)
        pbar.close()

        log.info(
            "✅ Обработано %d URL из Dorks. Этап: %s",
            len(dork_urls),
            _format_time((datetime.now() - phase_start).total_seconds()),
        )

        # --------------------------------------------------------------
        # ФАЗА 3: GitHub
        # --------------------------------------------------------------
        log.info("\n🐙 ЭТАП 3: GitHub")
        phase_start = datetime.now()

        github = GitHubScanner(token=GITHUB_TOKEN)
        gh_contacts = await github.scan(http)
        added_gh = 0

        for contact in gh_contacts:
            if contact.email not in contacts:
                if await mx.check(contact.domain):
                    contacts[contact.email] = contact
                    added_gh += 1

        log.info(
            "✅ GitHub добавил %d адресов. Этап: %s",
            added_gh,
            _format_time((datetime.now() - phase_start).total_seconds()),
        )

    # ------------------------------------------------------------------
    # Сохранение результатов
    # ------------------------------------------------------------------
    _save_checkpoint(checkpoint)
    repo.save(contacts)

    total_elapsed = (datetime.now() - start_time).total_seconds()
    log.info("=" * 60)
    log.info("🏁 РАБОТА ЗАВЕРШЕНА за %s", _format_time(total_elapsed))
    log.info("📊 ВСЕГО УНИКАЛЬНЫХ EMAIL: %d", len(contacts))
    log.info("💾 MX-кэш: %d доменов проверено", mx.cache_size)
    log.info("=" * 60)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("🛑 Прервано пользователем.")
        sys.exit(0)
