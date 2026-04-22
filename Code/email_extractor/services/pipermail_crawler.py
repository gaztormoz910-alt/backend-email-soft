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
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
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

        raw_str = _decode(raw)

        # Вытаскиваем все href="..." через регулярку (на 1000% быстрее и меньше ОЗУ, чем BeautifulSoup)
        list_links = [
            urllib.parse.urljoin(base, href)
            for href in re.findall(r'href="([^"]+)"', raw_str)
            if href.endswith("/") and not href.startswith("?")
        ]
        
        sem = asyncio.Semaphore(10) # 10 parallel lists per server

        async def _process_list(list_url: str):
            async with sem:
                list_raw = await client.fetch(list_url)
                if not list_raw:
                    return

                list_str = _decode(list_raw)
                month_links = [
                    urllib.parse.urljoin(list_url, href)
                    for href in re.findall(r'href="([^"]+)"', list_str)
                    if _MONTH_RE.match(href)
                ]

                for month_url in month_links:
                    month_raw = await client.fetch(month_url)
                    if not month_raw:
                        continue

                    await queue.put(month_url)
                    month_str = _decode(month_raw)

                    file_urls = [
                        urllib.parse.urljoin(month_url, href)
                        for href in re.findall(r'href="([^"]+)"', month_str)
                        if href.endswith((".html", ".htm"))
                    ]

                    for file_url in file_urls:
                        await queue.put(file_url)

        tasks = [asyncio.create_task(_process_list(url)) for url in list_links]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

