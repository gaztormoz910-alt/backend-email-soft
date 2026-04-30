"""
services/ddg_scanner.py
Сканер DuckDuckGo Dorks — ищет URL через DuckDuckGo Lite без капчи,
затем передаёт их в воркер-пул для извлечения email-адресов.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import AsyncGenerator
from urllib.parse import unquote

from ..core.interfaces import ISearchDiscovery, IHttpClient

log = logging.getLogger(__name__)


def _check_stop() -> bool:
    """Проверить, запрошена ли остановка/отмена."""
    try:
        import email_extractor.cli.main as core_main
        return getattr(core_main, '_STOP_REQUESTED', False) or getattr(core_main, '_CANCEL_REQUESTED', False)
    except Exception:
        return False


class DDGScanner(ISearchDiscovery):
    """
    Поиск URL через DuckDuckGo Lite.
    Не требует ключей, нет агрессивной капчи.
    Вдохновлено theHarvester.
    """

    def __init__(
        self,
        dorks: list[str],
        results_per_query: int = 10,
        sleep_between: float = 3.0,
    ) -> None:
        self._dorks = dorks
        self._results_per_query = results_per_query
        self._sleep = sleep_between

    async def discover(
        self,
        client: IHttpClient,
        known_urls: set[str] | None = None,
    ) -> AsyncGenerator[str, None]:
        
        if known_urls is None:
            known_urls = set()

        total = len(self._dorks)
        found_total = 0

        # Regex для поиска ссылок в DuckDuckGo Lite
        url_regex = re.compile(r'class="result-url"[^>]*href=["\'](?:/l/\?uddg=)?([^"\']+)["\']')

        for idx, dork in enumerate(self._dorks, 1):
            if _check_stop():
                break

            log.info("🦆 DDG Дорк [%d/%d]: %s", idx, total, dork)
            urls = []

            try:
                # Поиск через DuckDuckGo Lite (POST запрос)
                # lite.duckduckgo.com/lite/ работает без JavaScript
                response = await client.request(
                    method="POST",
                    url="https://lite.duckduckgo.com/lite/",
                    data={"q": dork, "s": "0", "o": "json"},
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                        "Content-Type": "application/x-www-form-urlencoded"
                    }
                )
                
                if response:
                    # Парсим URL
                    matches = url_regex.findall(response)
                    for raw_url in matches:
                        # DuckDuckGo иногда оборачивает ссылки, нужно их декодировать
                        clean_url = unquote(raw_url.split('&')[0])
                        if clean_url.startswith("http") and clean_url not in urls:
                            urls.append(clean_url)
                            if len(urls) >= self._results_per_query:
                                break
            except Exception as exc:
                log.warning("⚠ Ошибка DDG поиска для дорка '%s': %s", dork[:40], exc)

            new_urls = [u for u in urls if u not in known_urls]

            for url in new_urls:
                if _check_stop():
                    break
                known_urls.add(url)
                found_total += 1
                yield url

            if new_urls:
                log.info("   📎 DDG Дорк '%s' → %d новых URL", dork[:50], len(new_urls))

            await asyncio.sleep(self._sleep)

        log.info("🦆 DuckDuckGo Dorks: всего найдено %d URL из %d дорков", found_total, total)
