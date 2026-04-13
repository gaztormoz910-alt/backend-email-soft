"""
services/pipermail_crawler.py
Параллельный обход серверов Pipermail с прогресс-баром.

Реализует IPipermailCrawler.
Возвращает async-генератор URL, которые впоследствии обрабатывает cli/main.py.
"""
from __future__ import annotations

import asyncio
import logging
import re
import urllib.parse
from typing import AsyncGenerator

from bs4 import BeautifulSoup

from ..core.interfaces import IHttpClient, IPipermailCrawler

log = logging.getLogger(__name__)

# Паттерн для URL вида «2024-April/» или «2024-Apr/»
_MONTH_RE = re.compile(r"\d{4}-[A-Za-z]+/")


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            pass
    return raw.decode("utf-8", errors="replace")


class PipermailCrawler(IPipermailCrawler):
    """
    Обходит список Pipermail-серверов и выдаёт URL отдельных архивных страниц.

    Алгоритм для каждого сервера:
      1. Сгрузить корень → список рассылок (href, оканчивающиеся на «/»)
      2. Для каждой рассылки → список месячных архивов (href ~ YYYY-Month/)
      3. Для каждого месяца → список htm/html файлов
      4. Выдать: URL месяца + URL каждого файла
    """

    def __init__(self, servers: list[str]) -> None:
        self._servers = servers

    # ------------------------------------------------------------------
    # IPipermailCrawler implementation
    # ------------------------------------------------------------------

    async def discover(self, client: IHttpClient) -> AsyncGenerator[str, None]:  # type: ignore[override]
        """
        Асинхронный генератор URL.

        Все серверы обходятся параллельно; URL передаются через asyncio.Queue.
        """
        log.info("🔎 Параллельный обход %d серверов Pipermail…", len(self._servers))
        queue: asyncio.Queue[str] = asyncio.Queue()
        tasks = [
            asyncio.create_task(self._process_server(client, srv, queue))
            for srv in self._servers
        ]

        found = 0
        pending = set(tasks)

        while pending:
            done, pending = await asyncio.wait(pending, timeout=0.5)
            while not queue.empty():
                url = await queue.get()
                found += 1
                yield url

        # Выдать то, что накопилось после завершения всех задач
        while not queue.empty():
            url = await queue.get()
            found += 1
            yield url

        log.info("📬 Всего найдено %d страниц Pipermail", found)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _process_server(
        self,
        client: IHttpClient,
        base: str,
        queue: asyncio.Queue,
    ) -> None:
        raw = await client.fetch(base)
        if not raw:
            log.debug("✗ Недоступен сервер: %s", base)
            return

        soup = BeautifulSoup(_decode(raw), "html.parser")

        for a in soup.find_all("a", href=True):
            href: str = a["href"]
            if not href.endswith("/") or href.startswith("?"):
                continue

            list_url = urllib.parse.urljoin(base, href)
            list_raw = await client.fetch(list_url)
            if not list_raw:
                continue

            list_soup = BeautifulSoup(_decode(list_raw), "html.parser")

            for month_a in list_soup.find_all("a", href=True):
                month_href: str = month_a["href"]
                if not _MONTH_RE.match(month_href):
                    continue

                month_url = urllib.parse.urljoin(list_url, month_href)
                month_raw = await client.fetch(month_url)
                if not month_raw:
                    continue

                await queue.put(month_url)

                month_soup = BeautifulSoup(_decode(month_raw), "html.parser")
                for file_a in month_soup.find_all("a", href=True):
                    file_href: str = file_a["href"]
                    if file_href.endswith((".html", ".htm")):
                        await queue.put(urllib.parse.urljoin(month_url, file_href))
