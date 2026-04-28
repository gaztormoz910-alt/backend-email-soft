"""
services/dork_scanner.py
Сканер Google Dorks — ищет URL через Google Search, затем передаёт их
в воркер-пул для извлечения email-адресов.

Реализует ISearchDiscovery.
Использует googlesearch-python (уже в requirements.txt).
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator

from ..core.interfaces import ISearchDiscovery, IHttpClient

log = logging.getLogger(__name__)


def _check_stop() -> bool:
    """Проверить, запрошена ли остановка/отмена."""
    try:
        import email_extractor.cli.main as core_main
        return getattr(core_main, '_STOP_REQUESTED', False) or getattr(core_main, '_CANCEL_REQUESTED', False)
    except Exception:
        return False


class DorkScanner(ISearchDiscovery):
    """
    Поиск URL через Google Dorks (googlesearch-python).

    Для каждого дорка выполняет поиск в Google, собирает URL результатов
    и выдаёт их как async-генератор для последующей обработки воркер-пулом.

    Args:
        dorks:          Список поисковых запросов (дорков).
        results_per_query: Количество результатов на каждый дорк.
        sleep_between:  Пауза между запросами к Google (секунды).
    """

    def __init__(
        self,
        dorks: list[str],
        results_per_query: int = 10,
        sleep_between: float = 5.0,
    ) -> None:
        self._dorks = dorks
        self._results_per_query = results_per_query
        self._sleep = sleep_between

    # ------------------------------------------------------------------
    # ISearchDiscovery implementation
    # ------------------------------------------------------------------

    async def discover(
        self,
        client: IHttpClient,
        known_urls: set[str] | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        Асинхронный генератор URL, найденных через Google Dorks.

        googlesearch-python — синхронная библиотека, поэтому
        каждый вызов оборачивается в asyncio.to_thread().
        """
        if known_urls is None:
            known_urls = set()

        total = len(self._dorks)
        found_total = 0

        for idx, dork in enumerate(self._dorks, 1):
            if _check_stop():
                break

            log.info("🔍 Дорк [%d/%d]: %s", idx, total, dork)

            try:
                urls = await asyncio.to_thread(self._search_google, dork)
            except Exception as exc:
                log.warning("⚠ Ошибка Google поиска для дорка '%s': %s", dork[:40], exc)
                await asyncio.sleep(self._sleep)
                continue

            new_urls = [u for u in urls if u not in known_urls]

            for url in new_urls:
                if _check_stop():
                    break
                known_urls.add(url)
                found_total += 1
                yield url

            if new_urls:
                log.info("   📎 Дорк '%s' → %d новых URL", dork[:50], len(new_urls))

            # Пауза между дорками, чтобы Google не заблокировал
            await asyncio.sleep(self._sleep)

        log.info("🔍 Google Dorks: всего найдено %d URL из %d дорков", found_total, total)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _search_google(self, query: str) -> list[str]:
        """Синхронный поиск в Google (запускается в thread-pool)."""
        try:
            from googlesearch import search
            results = list(search(
                query,
                num_results=self._results_per_query,
                lang="en",
                sleep_interval=2,
            ))
            return results
        except Exception as exc:
            log.debug("Google search error: %s", exc)
            return []
