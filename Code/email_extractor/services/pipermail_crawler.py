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
        raw = None  # освобождаем буфер ответа немедленно

        list_links = [
            urllib.parse.urljoin(base, a["href"])
            for a in soup.find_all("a", href=True)
            if a["href"].endswith("/") and not a["href"].startswith("?")
        ]
        soup.decompose()  # явно освобождаем дерево BS4

        for list_url in list_links:
            list_raw = await client.fetch(list_url)
            if not list_raw:
                continue

            list_soup = BeautifulSoup(_decode(list_raw), "html.parser")
            list_raw = None  # освобождаем буфер

            month_links = [
                urllib.parse.urljoin(list_url, a["href"])
                for a in list_soup.find_all("a", href=True)
                if _MONTH_RE.match(a["href"])
            ]
            list_soup.decompose()  # явно освобождаем дерево BS4

            for month_url in month_links:
                month_raw = await client.fetch(month_url)
                if not month_raw:
                    continue

                await queue.put(month_url)

                month_soup = BeautifulSoup(_decode(month_raw), "html.parser")
                month_raw = None  # освобождаем буфер

                file_urls = [
                    urllib.parse.urljoin(month_url, a["href"])
                    for a in month_soup.find_all("a", href=True)
                    if a["href"].endswith((".html", ".htm"))
                ]
                month_soup.decompose()  # явно освобождаем дерево BS4

                for file_url in file_urls:
                    await queue.put(file_url)

