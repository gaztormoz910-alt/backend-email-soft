"""
services/ddg_scanner.py
Сканер DuckDuckGo — ищет URL через DuckDuckGo HTML-версию без капчи,
затем передаёт их в воркер-пул для извлечения email-адресов.

Использует GET-запрос к html.duckduckgo.com/html/ — работает без JS.
Вдохновлено theHarvester.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import AsyncGenerator
from urllib.parse import unquote, urlencode

from ..core.interfaces import ISearchDiscovery, IHttpClient

log = logging.getLogger(__name__)


def _check_stop() -> bool:
    """Проверить, запрошена ли остановка/отмена."""
    try:
        import email_extractor.cli.main as core_main
        return getattr(core_main, '_STOP_REQUESTED', False) or getattr(core_main, '_CANCEL_REQUESTED', False)
    except Exception:
        return False


# Regex для извлечения URL из href-ов DDG HTML-версии
# DDG оборачивает ссылки через //duckduckgo.com/l/?uddg=URL&...
_DDG_LINK_RE = re.compile(r'uddg=([^&"\']+)')
# Также бывают прямые ссылки в result__a
_DIRECT_LINK_RE = re.compile(r'class="result__a"[^>]*href="([^"]+)"')


class DDGScanner(ISearchDiscovery):
    """
    Поиск URL через DuckDuckGo HTML.
    Не требует ключей, нет агрессивной капчи.
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

        for idx, dork in enumerate(self._dorks, 1):
            if _check_stop():
                break

            log.info("🦆 DDG Дорк [%d/%d]: %s", idx, total, dork)
            urls: list[str] = []

            try:
                # GET-запрос к DuckDuckGo HTML (не требует JS)
                params = urlencode({"q": dork})
                search_url = f"https://html.duckduckgo.com/html/?{params}"

                raw = await client.fetch(search_url)
                if raw:
                    # Декодируем bytes -> str
                    try:
                        html = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        html = raw.decode("latin-1", errors="replace")

                    # 1. Ищем ссылки через uddg= параметр (основной формат DDG)
                    for match in _DDG_LINK_RE.finditer(html):
                        decoded_url = unquote(match.group(1))
                        if decoded_url.startswith("http") and decoded_url not in urls:
                            urls.append(decoded_url)
                            if len(urls) >= self._results_per_query:
                                break

                    # 2. Если uddg не нашли — fallback на прямые ссылки
                    if not urls:
                        for match in _DIRECT_LINK_RE.finditer(html):
                            href = match.group(1)
                            if href.startswith("http") and href not in urls:
                                urls.append(href)
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
