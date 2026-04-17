"""
services/google_dorks.py
Поиск URL через Google Dorks и Bing.

Реализует ISearchDiscovery.
Google-поиск выполняется синхронно в executor (googlesearch-python блокирующий).
Bing-поиск — через httpx с парсингом результатов.
"""
from __future__ import annotations

import asyncio
import logging
import random

from bs4 import BeautifulSoup

from ..core.interfaces import IHttpClient, ISearchDiscovery

log = logging.getLogger(__name__)


class GoogleDorksDiscovery(ISearchDiscovery):
    """
    Открывает URLs через Google Dorks + Bing для каждого запроса из *dorks*.

    Args:
        dorks:              Список поисковых запросов.
        results_per_query:  Сколько результатов брать на каждый дорк.
        sleep_between:      Пауза (сек.) между запросами (защита от бана).
    """

    def __init__(
        self,
        dorks: list[str],
        results_per_query: int = 10,
        sleep_between: float = 5.0,
    ) -> None:
        self._dorks = dorks
        self._n = results_per_query
        self._sleep = sleep_between

    # ------------------------------------------------------------------
    # ISearchDiscovery implementation
    # ------------------------------------------------------------------

    async def discover(
        self,
        client: IHttpClient,
        known_urls: set[str],
    ) -> list[str]:
        """
        Пройти все дорки, объединить результаты Google + Bing,
        вернуть только новые (не вошедшие в *known_urls*) URL.
        """
        discovered: list[str] = []
        seen: set[str] = known_urls.copy()

        for dork in self._dorks:
            log.debug("🔍 Дорк: %s", dork)
            g_res, b_res = await asyncio.gather(
                self._google(dork),
                self._bing(client, dork),
                return_exceptions=True,
            )
            for url in (
                (g_res if isinstance(g_res, list) else [])
                + (b_res if isinstance(b_res, list) else [])
            ):
                if url not in seen:
                    seen.add(url)
                    discovered.append(url)

            await asyncio.sleep(self._sleep)

        log.info("🔍 Дорки нашли %d уникальных URL", len(discovered))
        return discovered

    # ------------------------------------------------------------------
    # Internal search backends
    # ------------------------------------------------------------------

    async def _google(self, query: str) -> list[str]:
        """Запустить googlesearch в threadpool executor."""
        try:
            from googlesearch import search as _gs  # type: ignore[import]

            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                lambda: list(
                    _gs(
                        query,
                        num_results=self._n,
                        sleep_interval=self._sleep,
                        lang="en",
                        unique=True,
                    )
                ),
            )
        except Exception as exc:
            log.debug("Google search error: %s", exc)
            return []

    async def _bing(self, client: IHttpClient, query: str) -> list[str]:
        """Парсинг результатов Bing (без API)."""
        urls: list[str] = []
        seen: set[str] = set()

        for page in range(0, self._n, 10):
            try:
                # IHttpClient не поддерживает query-params напрямую — строим URL вручную
                import urllib.parse

                qs = urllib.parse.urlencode(
                    {"q": query, "first": page + 1, "count": 10, "FORM": "PERE"}
                )
                raw = await client.fetch(f"https://www.bing.com/search?{qs}")
                if not raw:
                    break

                soup = BeautifulSoup(raw.decode("utf-8", errors="replace"), "html.parser")
                anchors = soup.select("li.b_algo h2 a") or soup.select(".b_algo a[href]")

                for a in anchors:
                    href = a.get("href", "")
                    if href.startswith("http") and href not in seen:
                        seen.add(href)
                        urls.append(href)

                soup.decompose()  # явно освобождаем дерево BS4
                del soup, raw

                await asyncio.sleep(random.uniform(1, 2))
            except Exception as exc:
                log.debug("Bing search error: %s", exc)
                break

        return urls[: self._n]
