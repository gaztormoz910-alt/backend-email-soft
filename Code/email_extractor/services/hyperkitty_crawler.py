"""
services/hyperkitty_crawler.py
Краулер для HyperKitty (Mailman 3) серверов — Fedora и подобные.

URL-структура HyperKitty:
  - Список рассылок:   {base}/archives/
  - Конкретный лист:   {base}/archives/list/{listname}/
  - Месячный архив:    {base}/archives/list/{listname}/{year}/{month}/
  - Сообщение:         {base}/archives/list/{listname}/message/{msgid}/

Краулер:
  1. Перебирает заданные листы (из конфига).
  2. Для каждого листа берёт главную страницу → парсит ссылки на YYYY/M/.
  3. Для каждого месяца → парсит ссылки /message/XXXX/ на треды.
  4. Выдаёт URL каждого месяца и каждого сообщения как async-генератор.
"""
from __future__ import annotations

import asyncio
import logging
import re
import urllib.parse
from typing import AsyncGenerator

from ..core.interfaces import IHttpClient

log = logging.getLogger(__name__)

# Паттерн: /archives/list/xxx@yyy/2026/4/
_MONTH_URL_RE = re.compile(
    r'/archives/list/[^/]+@[^/]+/(\d{4})/(\d{1,2})/'
)
# Паттерн: /message/XXXXXX/
_MESSAGE_RE = re.compile(r'/message/([A-Z0-9]+)/')


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            pass
    return raw.decode("utf-8", errors="replace")


class HyperKittyCrawler:
    """
    Обходит HyperKitty-серверы и выдаёт URL сообщений для извлечения email.

    Args:
        servers:  Список словарей { "base": "https://...", "lists": ["a@b.org", ...] }
    """

    def __init__(self, servers: list[dict]) -> None:
        self._servers = servers

    async def discover(self, client: IHttpClient) -> AsyncGenerator[str, None]:
        """Асинхронный генератор URL из всех HyperKitty серверов."""
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)

        total_lists = sum(len(s.get("lists", [])) for s in self._servers)
        log.info("🔎 HyperKitty: обход %d рассылок...", total_lists)

        tasks = []
        for server in self._servers:
            base = server["base"].rstrip("/")
            for listname in server.get("lists", []):
                tasks.append(
                    asyncio.create_task(
                        self._process_list(client, base, listname, queue)
                    )
                )

        found = 0
        pending = set(tasks)

        while pending:
            done, pending = await asyncio.wait(pending, timeout=0.5)
            while not queue.empty():
                url = await queue.get()
                found += 1
                yield url

        # Остаток из очереди
        while not queue.empty():
            url = await queue.get()
            found += 1
            yield url

        log.info("📬 HyperKitty: всего найдено %d страниц", found)

    async def _process_list(
        self,
        client: IHttpClient,
        base: str,
        listname: str,
        queue: asyncio.Queue,
    ) -> None:
        """Обработка одного списка рассылки."""
        list_url = f"{base}/archives/list/{listname}/"
        raw = await client.fetch(list_url)
        if not raw:
            log.debug("✗ HyperKitty: недоступен %s", listname)
            return

        html = _decode(raw)

        # Собираем все ссылки на месячные архивы: /archives/list/xxx@yyy/YYYY/M/
        month_hrefs: set[str] = set()
        for href in re.findall(r'href="([^"]+)"', html):
            if _MONTH_URL_RE.search(href):
                full = urllib.parse.urljoin(list_url, href)
                month_hrefs.add(full)

        # Ограничиваем параллелизм — не больше 5 месяцев одновременно
        sem = asyncio.Semaphore(5)

        async def _process_month(month_url: str):
            async with sem:
                month_raw = await client.fetch(month_url)
                if not month_raw:
                    return

                await queue.put(month_url)  # Сама страница месяца содержит email в тредах

                month_html = _decode(month_raw)

                # Собираем ссылки на отдельные сообщения: /message/XXXXX/
                msg_urls: set[str] = set()
                for href in re.findall(r'href="([^"]+)"', month_html):
                    if _MESSAGE_RE.search(href):
                        full = urllib.parse.urljoin(month_url, href)
                        msg_urls.add(full)

                for msg_url in msg_urls:
                    await queue.put(msg_url)

        month_tasks = [
            asyncio.create_task(_process_month(url))
            for url in sorted(month_hrefs, reverse=True)  # Новые месяцы сначала
        ]
        if month_tasks:
            await asyncio.gather(*month_tasks, return_exceptions=True)
