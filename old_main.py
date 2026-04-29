#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli/main.py ΓÇö ╨ó╨╛╤ç╨║╨░ ╨▓╤à╨╛╨┤╨░ EMAIL EXTRACTOR v12.1 (Memory-Optimized)

╨ù╨░╨┐╤â╤ü╨║:
    python -m email_extractor.cli.main
    # ╨╕╨╗╨╕
    python Code/email_extractor/cli/main.py

╨º╨╡╤é╤ï╤Ç╨╡ ╤ä╨░╨╖╤ï ╨▓╤ï╨┐╨╛╨╗╨╜╤Å╤Ä╤é╤ü╤Å ╨┐╨╛╤ü╨╗╨╡╨┤╨╛╨▓╨░╤é╨╡╨╗╤î╨╜╨╛:
    0. ╨¢╨╛╨║╨░╨╗╤î╨╜╤ï╨╡ ╤ä╨░╨╣╨╗╤ï (LocalFileScanner)
    1. Pipermail-╨░╤Ç╤à╨╕╨▓╤ï (PipermailCrawler)
    3. GitHub ╨║╨╛╨╝╨╝╨╕╤é╤ï (GitHubScanner)
    4. COMB API (ProxyNova ΓÇö ╨▒╨╡╤ü╨┐╨╗╨░╤é╨╜╨░╤Å ╨▒╨░╨╖╨░ ╤â╤é╨╡╤ç╨╡╨║)

╨₧╨┐╤é╨╕╨╝╨╕╨╖╨░╤å╨╕╤Å ╨┐╨░╨╝╤Å╤é╨╕ v12.1:
    - checkpoint["processed"] ╨┐╨╡╤Ç╨╡╨╜╨╡╤ü╤æ╨╜ ╨╕╨╖ RAM ╨▓ SQLite (╤é╨░╨▒╨╗╨╕╤å╨░ processed_urls)
    - MAX_CONCURRENT ╤ü╨╜╨╕╨╢╨╡╨╜ ╨┤╨╛ 20 (╨▒╤ï╨╗╨╛ 100)
    - MAX_MB ╤ü╨╜╨╕╨╢╨╡╨╜ ╨┤╨╛ 5 (╨▒╤ï╨╗╨╛ 20)
    - BS4 ╨╛╨▒╤è╨╡╨║╤é╤ï ╤Å╨▓╨╜╨╛ ╨╛╤ü╨▓╨╛╨▒╨╛╨╢╨┤╨░╤Ä╤é╤ü╤Å ╤ç╨╡╤Ç╨╡╨╖ decompose()
    - MxChecker ╨╕╤ü╨┐╨╛╨╗╤î╨╖╤â╨╡╤é LRU-╨║╤ì╤ê ╤ü ╨╗╨╕╨╝╨╕╤é╨╛╨╝ 5000 ╨┤╨╛╨╝╨╡╨╜╨╛╨▓
"""
from __future__ import annotations

import asyncio
import gc
import logging
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# ╨ƒ╨╛╨┤╨░╨▓╨╗╨╡╨╜╨╕╨╡ ╤ê╤â╨╝╨╜╤ï╤à ╨╗╨╛╨│╨│╨╡╤Ç╨╛╨▓ ╤ü╤é╨╛╤Ç╨╛╨╜╨╜╨╕╤à ╨▒╨╕╨▒╨╗╨╕╨╛╤é╨╡╨║
# ---------------------------------------------------------------------------
import logging as _lg

_lg.getLogger("fake_useragent").setLevel(_lg.ERROR)
_lg.getLogger("httpx").setLevel(_lg.WARNING)
_lg.getLogger("dns").setLevel(_lg.WARNING)

# ---------------------------------------------------------------------------
# tqdm ΓÇö ╨╝╤Å╨│╨║╨╕╨╣ ╨╕╨╝╨┐╨╛╤Ç╤é
# ---------------------------------------------------------------------------
try:
    from tqdm.asyncio import tqdm as async_tqdm  # type: ignore[import]
    from tqdm import tqdm as sync_tqdm  # type: ignore[import]

    TQDM_OK = True
except ImportError:
    TQDM_OK = False

    class _FakeTqdm:
        """╨ù╨░╨│╨╗╤â╤ê╨║╨░ tqdm ╨┤╨╗╤Å ╤ü╨╗╤â╤ç╨░╤Å, ╨║╨╛╨│╨┤╨░ ╨▒╨╕╨▒╨╗╨╕╨╛╤é╨╡╨║╨░ ╨╜╨╡ ╤â╤ü╤é╨░╨╜╨╛╨▓╨╗╨╡╨╜╨░."""

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
# ╨ÿ╨╝╨┐╨╛╤Ç╤é╤ï ╨▓╨╜╤â╤é╤Ç╨╕ ╨┐╨░╨║╨╡╤é╨░
# ---------------------------------------------------------------------------
# ╨ô╨░╤Ç╨░╨╜╤é╨╕╤Ç╤â╨╡╨╝, ╤ç╤é╨╛ ╨║╨╛╤Ç╨╡╨╜╤î workspace ╨▓ sys.path (╨┤╨╗╤Å ╨╖╨░╨┐╤â╤ü╨║╨░ ╨║╨░╨║ ╤ü╨║╤Ç╨╕╨┐╤é╨░)
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
    LOCAL_SCAN_DIR,
    LOG_DATE_FORMAT,
    LOG_FORMAT,
    LOG_LEVEL,
    MAX_CONCURRENT,
    MAX_MB,
    MEMORY_LIMIT_MB,
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
from email_extractor.services.local_file_scanner import LocalFileScanner
from email_extractor.services.mx_checker import MxChecker
from email_extractor.services.pipermail_crawler import PipermailCrawler
from email_extractor.services.comb_scanner import CombApiScanner
from websocket_manager import ws_manager

# ---------------------------------------------------------------------------
# ╨Æ╤ü╨┐╨╛╨╝╨╛╨│╨░╤é╨╡╨╗╤î╨╜╤ï╨╡ ╤ä╤â╨╜╨║╤å╨╕╨╕
# ---------------------------------------------------------------------------

log = logging.getLogger(__name__)


def _format_time(seconds: float) -> str:
    """╨ñ╨╛╤Ç╨╝╨░╤é╨╕╤Ç╨╛╨▓╨░╤é╤î ╤ü╨╡╨║╤â╨╜╨┤╤ï ╨▓ ╤ç╨╕╤é╨░╨╡╨╝╤â╤Ä ╤ü╤é╤Ç╨╛╨║╤â (N╤ç N╨╝ N╤ü)."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}╤ç {m}╨╝ {s}╤ü"
    if m > 0:
        return f"{m}╨╝ {s}╤ü"
    return f"{s}╤ü"


def _touch_checkpoint() -> None:
    """╨í╨╛╨╖╨┤╨░╤é╤î ╨┐╤â╤ü╤é╨╛╨╣ ╤ä╨░╨╣╨╗-╨╝╨░╤Ç╨║╨╡╤Ç checkpoint.json (╨╖╨░╤ë╨╕╤é╨░ ╨╛╤é ╨╛╤ç╨╕╤ü╤é╨║╨╕ ╨æ╨ö ╨┐╤Ç╨╕ ╤Ç╨╡╤ü╤é╨░╤Ç╤é╨╡)."""
    try:
        CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
        CHECKPOINT_FILE.touch(exist_ok=True)
    except Exception as exc:
        log.warning("ΓÜá ╨¥╨╡ ╤â╨┤╨░╨╗╨╛╤ü╤î ╤ü╨╛╨╖╨┤╨░╤é╤î checkpoint-╨╝╨░╤Ç╨║╨╡╤Ç: %s", exc)


def _get_memory_mb() -> float:
    """╨ƒ╨╛╨╗╤â╤ç╨╕╤é╤î ╤é╨╡╨║╤â╤ë╨╡╨╡ ╨┐╨╛╤é╤Ç╨╡╨▒╨╗╨╡╨╜╨╕╨╡ RAM ╨┐╤Ç╨╛╤å╨╡╤ü╤ü╨╛╨╝ (MB). ╨Ü╤Ç╨╛╤ü╤ü╨┐╨╗╨░╤é╤ä╨╛╤Ç╨╝╨╡╨╜╨╜╨╛."""
    try:
        import resource
        # Linux/Mac: ru_maxrss ╨▓ KB
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return usage.ru_maxrss / 1024  # KB -> MB
    except ImportError:
        pass
    try:
        # Fallback: ╤ç╨╕╤é╨░╨╡╨╝ /proc/self/status (Linux / Railway)
        with open("/proc/self/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024  # KB -> MB
    except Exception:
        pass
    return 0.0


# ---------------------------------------------------------------------------
# ╨₧╨▒╤Ç╨░╨▒╨╛╤é╨║╨░ ╨╛╨┤╨╜╨╛╨│╨╛ URL
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
    """╨í╨║╨░╤ç╨░╤é╤î URL ╨┐╨╛╤é╨╛╨║╨╛╨▓╨╛, ╨╕╨╖╨▓╨╗╨╡╤ç╤î ╨║╨╛╨╜╤é╨░╨║╤é╤ï, ╨┤╨╛╨▒╨░╨▓╨╕╤é╤î ╨▓ ╨╛╤ç╨╡╤Ç╨╡╨┤╤î ╨╖╨░╨┐╨╕╤ü╨╕ ╨æ╨ö."""
    async with sem:
        if _STOP_REQUESTED or _CANCEL_REQUESTED:
            return 0

        if repo.is_url_processed(url):
            return 0

        # ╨£╨│╨╜╨╛╨▓╨╡╨╜╨╜╨░╤Å ╨┐╨╛╨╝╨╡╤é╨║╨░ ╨▓ LRU-╨║╤ì╤ê╨╡ ╨┤╨╗╤Å ╨┤╤Ç╤â╨│╨╕╤à ╨▓╨╛╤Ç╨║╨╡╤Ç╨╛╨▓
        repo._cache_add(url)
        db_url_queue.put_nowait(url)

        stream = http.stream_lines(url)
        found = await extractor.extract_from_stream(stream, url)
        
        added = 0
        for contact in found:
            e = contact.email
            if not e or is_fake_email(e):
                continue
            if not await mx.check(contact.domain):
                continue
            db_contact_queue.put_nowait(contact)
            added += 1

        if added > 0 or len(found) > 0:
            msg = f"   ≡ƒöÄ ╨¥╨░ ╤ü╤é╤Ç╨░╨╜╨╕╤å╨╡ ╨╜╨░╨╣╨┤╨╡╨╜╨╛: {len(found)} | ╨ƒ╤Ç╨╛╤ê╨╗╨╕ ╤ä╨╕╨╗╤î╤é╤Ç: {added} -> {url[:70]}"
            log.info(msg)
            asyncio.create_task(ws_manager.send_log(msg))

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
        log.warning("ΓÜá ╨¡╨║╤ü╤é╤Ç╨░╨║╤å╨╕╤Å ╤â╨╢╨╡ ╨╖╨░╨┐╤â╤ë╨╡╨╜╨░. ╨ƒ╨╛╨▓╤é╨╛╤Ç╨╜╤ï╨╣ ╨▓╤ï╨╖╨╛╨▓ ╨┐╤Ç╨╛╨┐╤â╤ë╨╡╨╜.")
        return
    _IS_RUNNING = True
    _STOP_REQUESTED = False
    _CANCEL_REQUESTED = False
    _CURRENT_TASK = asyncio.current_task()
    try:
        await _main_logic()
    except asyncio.CancelledError:
        log.warning("≡ƒ¢æ ╨ù╨É╨ö╨É╨º╨É ╨æ╨½╨¢╨É ╨û╨ò╨í╨ó╨Ü╨₧ ╨₧╨ó╨£╨ò╨¥╨ò╨¥╨É/╨₧╨í╨ó╨É╨¥╨₧╨Æ╨¢╨ò╨¥╨É")
        if _CANCEL_REQUESTED:
            ws_manager.clear_history()
            ws_manager.email_count = 0
            asyncio.create_task(ws_manager.send_count(0))
            # ╨ú╨┤╨░╨╗╤Å╨╡╨╝ ╨╝╨░╤Ç╨║╨╡╤Ç ╨╕ ╨╛╤ç╨╕╤ë╨░╨╡╨╝ ╨æ╨ö ╨┐╨╛╨╗╨╜╨╛╤ü╤é╤î╤Ä
            if CHECKPOINT_FILE.exists():
                CHECKPOINT_FILE.unlink()
            repo = SqliteContactRepository(db_path=DB_OUTPUT)
            repo.clear_all()
            repo.clear_processed_urls()
        elif _STOP_REQUESTED:
            # ╨ƒ╤Ç╨╕ ╨┐╨░╤â╨╖╨╡ ΓÇö ╨┤╨░╨╜╨╜╤ï╨╡ ╤ü╨╛╤à╤Ç╨░╨╜╨╡╨╜╤ï ╨▓ SQLite, ╨╝╨░╤Ç╨║╨╡╤Ç ╨╛╤ü╤é╨░╨▓╨╗╤Å╨╡╨╝
            pass
    except Exception as exc:
        log.error("≡ƒÆÑ ╨₧╤ê╨╕╨▒╨║╨░ ╨┐╨░╤Ç╤ü╨╕╨╜╨│╨░: %s", exc, exc_info=True)
        raise  # ╨ƒ╤Ç╨╛╨▒╤Ç╨░╤ü╤ï╨▓╨░╨╡╨╝ ╨╛╤ê╨╕╨▒╨║╤â ╨▓╤ï╤ê╨╡, ╤ç╤é╨╛╨▒╤ï engine.py ╨╝╨╛╨│ ╨┐╨╛╨╣╨╝╨░╤é╤î ╨╡╨╡ ╨╕ ╨╖╨░╨┐╤â╤ü╤é╨╕╤é╤î ╨░╨▓╤é╨╛-╨▓╨╛╨╖╨╛╨▒╨╜╨╛╨▓╨╗╨╡╨╜╨╕╨╡
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
    # Worker ╨┤╨╗╤Å ╨┐╨░╤Ç╨░╨╗╨╗╨╡╨╗╤î╨╜╨╛╨╣ ╨╛╨▒╤Ç╨░╨▒╨╛╤é╨║╨╕ URL (╨▒╨╡╨╖ ╤ü╨╛╤ü╤é╨╛╤Å╨╜╨╕╤Å ╨▓ RAM)
    # ------------------------------------------------------------------
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

    _START_TIME = datetime.now().timestamp()
    start_time = datetime.now()

    ws_manager.clear_history()

    msg_start = "≡ƒÜÇ ╨ù╨É╨ƒ╨ú╨í╨Ü EMAIL EXTRACTOR v12.1 ΓÇö MEMORY OPTIMIZED"
    log.info("=" * 60)
    log.info(msg_start)
    log.info("=" * 60)
    asyncio.create_task(ws_manager.send_log("=" * 60))
    asyncio.create_task(ws_manager.send_log(msg_start))
    asyncio.create_task(ws_manager.send_log("=" * 60))

    # ------------------------------------------------------------------
    # ╨ÿ╨╜╨╕╤å╨╕╨░╨╗╨╕╨╖╨░╤å╨╕╤Å ╨║╨╛╨╝╨┐╨╛╨╜╨╡╨╜╤é╨╛╨▓
    # ------------------------------------------------------------------
    repo = SqliteContactRepository(db_path=DB_OUTPUT)

    # ╨ò╤ü╨╗╨╕ ╨╝╨░╤Ç╨║╨╡╤Ç-╤ä╨░╨╣╨╗ ╨╛╤é╤ü╤â╤é╤ü╤é╨▓╤â╨╡╤é ΓåÆ ╨╜╨╛╨▓╤ï╨╣ ╨╖╨░╨┐╤â╤ü╨║ ΓåÆ ╨╛╤ç╨╕╤ë╨░╨╡╨╝ ╨▒╨░╨╖╤â ╨┐╨╛╨╗╨╜╨╛╤ü╤é╤î╤Ä.
    # ╨ò╤ü╨╗╨╕ ╨╝╨░╤Ç╨║╨╡╤Ç ╨╡╤ü╤é╤î ΓåÆ ╨┐╤Ç╨╛╨┤╨╛╨╗╨╢╨╡╨╜╨╕╨╡ ╨┐╨╛╤ü╨╗╨╡ ╨┐╨░╤â╨╖╤ï/╨║╤Ç╨░╤ê╨░ ΓåÆ ╨┤╨░╨╜╨╜╤ï╨╡ ╤ü╨╛╤à╤Ç╨░╨╜╤Å╨╡╨╝.
    if not CHECKPOINT_FILE.exists():
        repo.clear_all()
        repo.clear_processed_urls()
        ws_manager.email_count = 0
    else:
        # ╨Æ╨╛╤ü╤ü╤é╨░╨╜╨░╨▓╨╗╨╕╨▓╨░╨╡╨╝ ╤ü╤ç╨╡╤é╤ç╨╕╨║, ╤é╨░╨║ ╨║╨░╨║ clear_history ╤ü╨▒╤Ç╨╛╤ü╨╕╨╗ ╨╡╨│╨╛ ╨▓ 0 ╨┐╨╡╤Ç╨╡╨┤ ╤ü╤é╨░╤Ç╤é╨╛╨╝
        count = repo.get_count()
        ws_manager.email_count = count
        asyncio.create_task(ws_manager.send_count(count))

    # ------------------------------------------------------------------
    # Batch Writer (╨┤╨╗╤Å SQLite)
    # ------------------------------------------------------------------
    db_contact_queue: asyncio.Queue = asyncio.Queue()
    db_url_queue: asyncio.Queue = asyncio.Queue()

    async def _db_writer() -> None:
        gc_counter = 0
        while True:
            try:
                if _STOP_REQUESTED or _CANCEL_REQUESTED:
                    break
                contacts_to_add = []
                urls_to_mark = []
                while not db_url_queue.empty():
                    urls_to_mark.append(db_url_queue.get_nowait())
                    db_url_queue.task_done()
                while not db_contact_queue.empty():
                    contacts_to_add.append(db_contact_queue.get_nowait())
                    db_contact_queue.task_done()

                if urls_to_mark:
                    repo.mark_urls_processed_bulk(urls_to_mark)
                if contacts_to_add:
                    added = repo.add_contacts_bulk(contacts_to_add)
                    duplicates = len(contacts_to_add) - added
                    if added > 0 or duplicates > 0:
                        msg_db = f"   ≡ƒÆ╛ ╨æ╨É╨ù╨É ╨ö╨É╨¥╨¥╨½╨Ñ: ╨ú╤ü╨┐╨╡╤ê╨╜╨╛ ╤ü╨╛╤à╤Ç╨░╨╜╨╡╨╜╨╛ ╨¥╨₧╨Æ╨½╨Ñ: {added} | ╨₧╤é╨▒╤Ç╨╛╤ê╨╡╨╜╨╛ ╨ö╨ú╨æ╨¢╨ÿ╨Ü╨É╨ó╨₧╨Æ: {duplicates}"
                        log.info(msg_db)
                        asyncio.create_task(ws_manager.send_log(msg_db))
                    
                    if added > 0:
                        asyncio.create_task(ws_manager.send_count(repo.get_count()))

                # ╨ƒ╨╡╤Ç╨╕╨╛╨┤╨╕╤ç╨╡╤ü╨║╨╕╨╣ GC + ╨╝╨╛╨╜╨╕╤é╨╛╤Ç╨╕╨╜╨│ ╨┐╨░╨╝╤Å╤é╨╕ (╨║╨░╨╢╨┤╤ï╨╡ ~12 ╤ü╨╡╨║)
                gc_counter += 1
                if gc_counter >= 60:  # 60 * 0.2s = 12 ╤ü╨╡╨║
                    gc_counter = 0
                    gc.collect()
                    mem_mb = _get_memory_mb()
                    if mem_mb > 0:
                        if mem_mb > MEMORY_LIMIT_MB:
                            msg = f"ΓÜá∩╕Å ╨ƒ╨É╨£╨»╨ó╨¼: {mem_mb:.0f}MB > {MEMORY_LIMIT_MB}MB ΓÇö ╤é╨╛╤Ç╨╝╨╛╨╢╤â ╨╜╨░ 10╤ü + GC..."
                            log.warning(msg)
                            asyncio.create_task(ws_manager.send_log(msg))
                            gc.collect()
                            await asyncio.sleep(10)
                            gc.collect()

                await asyncio.sleep(0.2)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"DB Writer error: {e}")
                await asyncio.sleep(1)

    db_writer_task = asyncio.create_task(_db_writer())

    mx = MxChecker()
    extractor = EmailExtractorService()
    sem = asyncio.Semaphore(MAX_CONCURRENT)

    # ╨í╨╛╨╖╨┤╨░╤æ╨╝ ╨╝╨░╤Ç╨║╨╡╤Ç-╤ä╨░╨╣╨╗ ╨í╨á╨É╨ù╨ú ΓÇö ╨╖╨░╤ë╨╕╤é╨░ ╨╛╤é ╨╛╤ç╨╕╤ü╤é╨║╨╕ ╨æ╨ö ╨┐╤Ç╨╕ ╨║╤Ç╨░╤ê╨╡/OOM
    _touch_checkpoint()

    processed_before = repo.get_processed_count()
    log.info("≡ƒôî ╨ú╨╢╨╡ ╨╛╨▒╤Ç╨░╨▒╨╛╤é╨░╨╜╨╛ URL (╨╕╨╖ ╨┐╤Ç╨╛╤ê╨╗╤ï╤à ╨╖╨░╨┐╤â╤ü╨║╨╛╨▓): %d", processed_before)

    # ------------------------------------------------------------------
    # ╨ñ╨É╨ù╨É 0: ╨¢╨╛╨║╨░╨╗╤î╨╜╤ï╨╡ ╤ä╨░╨╣╨╗╤ï
    # ------------------------------------------------------------------
    log.info("\n≡ƒôé ╨¡╨ó╨É╨ƒ 0: ╨¢╨╛╨║╨░╨╗╤î╨╜╨╛╨╡ ╤ü╨║╨░╨╜╨╕╤Ç╨╛╨▓╨░╨╜╨╕╨╡")
    phase_start = datetime.now()

    scanner = LocalFileScanner(extensions={"*"})
    local_contacts: list[Contact] = await asyncio.to_thread(scanner.scan, LOCAL_SCAN_DIR)

    with sync_tqdm(local_contacts, desc="≡ƒôé ╨ñ╨╕╨╗╤î╤é╤Ç╨░╤å╨╕╤Å ╨╗╨╛╨║╨░╨╗╤î╨╜╤ï╤à", unit="╤ê╤é") as pbar:
        added_local = 0
        for c in pbar:
            if _STOP_REQUESTED or _CANCEL_REQUESTED:
                break
            if not is_fake_email(c.email):
                if repo.add_if_not_exists(c):
                    added_local += 1

    if added_local > 0:
        asyncio.create_task(ws_manager.send_count(repo.get_count()))

    msg_local = f"Γ£à ╨¢╨╛╨║╨░╨╗╤î╨╜╤ï╨╡ ╤ä╨░╨╣╨╗╤ï ╨┤╨╛╨▒╨░╨▓╨╕╨╗╨╕ {added_local} ╨░╨┤╤Ç╨╡╤ü╨╛╨▓. ╨¡╤é╨░╨┐: {_format_time((datetime.now() - phase_start).total_seconds())}"
    log.info(msg_local)
    asyncio.create_task(ws_manager.send_log(msg_local))

    # ------------------------------------------------------------------
    # ╨₧╤é╨║╤Ç╤ï╨▓╨░╨╡╨╝ HTTP-╨║╨╗╨╕╨╡╨╜╤é ╨┤╨╗╤Å ╤ü╨╡╤é╨╡╨▓╤ï╤à ╤ä╨░╨╖
    # ------------------------------------------------------------------
    async with AsyncHttpClient(
        timeout=REQUEST_TIMEOUT,
        max_mb=MAX_MB,
        max_connections=MAX_CONCURRENT * 2,
        max_keepalive=MAX_CONCURRENT,
    ) as http:

        # --------------------------------------------------------------
        # ╨ƒ╤â╨╗ ╨▓╨╛╤Ç╨║╨╡╤Ç╨╛╨▓ (╨┐╨╛╨▓╤é╨╛╤Ç╨╜╨╛ ╨╕╤ü╨┐╨╛╨╗╤î╨╖╤â╨╡╤é╤ü╤Å ╨┤╨╗╤Å ╨▓╤ü╨╡╤à ╤ü╨╡╤é╨╡╨▓╤ï╤à ╤ä╨░╨╖)
        # --------------------------------------------------------------
        url_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
        workers = []

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
        # ╨ñ╨É╨ù╨É 1: COMB API (ProxyNova)
        # --------------------------------------------------------------
        if not (_STOP_REQUESTED or _CANCEL_REQUESTED):
            log.info("\n≡ƒöô ╨¡╨ó╨É╨ƒ 1: COMB API (ProxyNova ΓÇö ╨┐╤â╨▒╨╗╨╕╤ç╨╜╨░╤Å ╨▒╨░╨╖╨░ ╤â╤é╨╡╤ç╨╡╨║)")
            asyncio.create_task(ws_manager.send_log("≡ƒöô ╨¡╨ó╨É╨ƒ 1: COMB API (╨┐╤â╨▒╨╗╨╕╤ç╨╜╨░╤Å ╨▒╨░╨╖╨░ ╤â╤é╨╡╤ç╨╡╨║)..."))
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
                    db_contact_queue.put_nowait(contact)
                    added_comb += 1
                    
            msg_comb = f"Γ£à COMB API: +{added_comb} ╨░╨┤╤Ç╨╡╤ü╨╛╨▓ ╨╕╨╖ {len(COMB_DOMAINS)} ╨┤╨╛╨╝╨╡╨╜╨╛╨▓. ╨¡╤é╨░╨┐: {_format_time((datetime.now() - phase_start).total_seconds())}"
            log.info(msg_comb)
            asyncio.create_task(ws_manager.send_log(msg_comb))

    
# --------------------------------------------------------------
        # ╨ñ╨É╨ù╨É 2: Pipermail
        # --------------------------------------------------------------
        log.info("\n≡ƒôº ╨¡╨ó╨É╨ƒ 2: ╨É╤Ç╤à╨╕╨▓╤ï Pipermail")
        phase_start = datetime.now()
        crawler = PipermailCrawler(servers=PIPERMAIL_SERVERS)
        pbar = async_tqdm(desc="╨₧╨▒╤Ç╨░╨▒╨╛╤é╨║╨░ ╤ü╤é╤Ç╨░╨╜╨╕╤å", unit="╤ü╤é╤Ç", position=0, total=None)
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

        msg_piper = f"Γ£à ╨₧╨▒╤Ç╨░╨▒╨╛╤é╨░╨╜╨╛ {discovered_count} ╤ü╤é╤Ç╨░╨╜╨╕╤å Pipermail. ╨¡╤é╨░╨┐: {_format_time((datetime.now() - phase_start).total_seconds())}"
        log.info(msg_piper)
        asyncio.create_task(ws_manager.send_log(msg_piper))

        # --------------------------------------------------------------
        # ╨ñ╨É╨ù╨É 3: GitHub
        # --------------------------------------------------------------
        log.info("\n≡ƒÉÖ ╨¡╨ó╨É╨ƒ 3: GitHub")
        phase_start = datetime.now()

        github = GitHubScanner(token=GITHUB_TOKEN)
        gh_contacts = await github.scan(http)
        added_gh = 0

        for contact in gh_contacts:
            if _STOP_REQUESTED or _CANCEL_REQUESTED:
                break
            if await mx.check(contact.domain):
                db_contact_queue.put_nowait(contact)
                added_gh += 1

        if added_gh > 0:
            msg_gh = f"   ≡ƒÄ» ╨ƒ╤Ç╨╛╤å╨╡╤ü╤ü╨╕╨╜╨│ GitHub: +{added_gh} ╨░╨┤╤Ç╨╡╤ü╨╛╨▓"
            log.info(msg_gh)
        asyncio.create_task(ws_manager.send_log(msg_gh))

        # ------------------------------------------------------------------
    # ╨ù╨░╨▓╨╡╤Ç╤ê╨╡╨╜╨╕╨╡
    # ------------------------------------------------------------------
    db_writer_task.cancel()
    # ╨ñ╨╕╨╜╨░╨╗╤î╨╜╤ï╨╣ ╤ü╨▒╤Ç╨╛╤ü ╨╛╤ç╨╡╤Ç╨╡╨┤╨╡╨╣ ╨▓ ╨▒╨░╨╖╤â
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

        msg_cancel = "≡ƒ¢æ ╨í╨ò╨í╨í╨ÿ╨» ╨₧╨ó╨£╨ò╨¥╨ò╨¥╨É. ╨ƒ╤Ç╨╛╨│╤Ç╨╡╤ü╤ü ╨┐╨╛╨╗╨╜╨╛╤ü╤é╤î╤Ä ╤ü╤é╤æ╤Ç╤é. ╨ô╨╛╤é╨╛╨▓ ╨║ ╨╜╨╛╨▓╨╛╨╝╤â ╤ü╤é╨░╤Ç╤é╤â."
        log.warning(msg_cancel)
        asyncio.create_task(ws_manager.send_log(msg_cancel))

        # ╨ú╨┤╨░╨╗╤Å╨╡╨╝ ╨╝╨░╤Ç╨║╨╡╤Ç ΓÇö ╤ü╨╗╨╡╨┤╤â╤Ä╤ë╨╕╨╣ ╤ü╤é╨░╤Ç╤é ╨╜╨░╤ç╨╜╤æ╤é ╤ü ╤ç╨╕╤ü╤é╨╛╨│╨╛ ╨╗╨╕╤ü╤é╨░
        if CHECKPOINT_FILE.exists():
            CHECKPOINT_FILE.unlink()
    # else: ╨╝╨░╤Ç╨║╨╡╤Ç ╨╛╤ü╤é╨░╤æ╤é╤ü╤Å ΓÇö ╤ü╨╗╨╡╨┤╤â╤Ä╤ë╨╕╨╣ ╨╖╨░╨┐╤â╤ü╨║ ╨┐╤Ç╨╛╨┤╨╛╨╗╨╢╨╕╤é ╤ü ╤é╨╛╨│╨╛ ╨╢╨╡ ╨╝╨╡╤ü╤é╨░

    total_elapsed = (datetime.now() - start_time).total_seconds()
    total_emails = repo.get_count()
    total_processed = repo.get_processed_count()

    msg_end = (
        f"{'=' * 60}\n"
        f"≡ƒÅü ╨á╨É╨æ╨₧╨ó╨É ╨ù╨É╨Æ╨ò╨á╨¿╨ò╨¥╨É ╨╖╨░ {_format_time(total_elapsed)}\n"
        f"≡ƒôè ╨Æ╨í╨ò╨ô╨₧ ╨ú╨¥╨ÿ╨Ü╨É╨¢╨¼╨¥╨½╨Ñ EMAIL: {total_emails}\n"
        f"≡ƒöù ╨₧╨▒╤Ç╨░╨▒╨╛╤é╨░╨╜╨╛ URL: {total_processed}\n"
        f"≡ƒÆ╛ MX-╨║╤ì╤ê: {mx.cache_size} ╨┤╨╛╨╝╨╡╨╜╨╛╨▓ ╨┐╤Ç╨╛╨▓╨╡╤Ç╨╡╨╜╨╛\n"
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
        logging.info("≡ƒ¢æ ╨ƒ╤Ç╨╡╤Ç╨▓╨░╨╜╨╛ ╨┐╨╛╨╗╤î╨╖╨╛╨▓╨░╤é╨╡╨╗╨╡╨╝.")
        sys.exit(0)
