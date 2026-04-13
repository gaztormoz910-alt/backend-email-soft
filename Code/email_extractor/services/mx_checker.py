"""
services/mx_checker.py
Проверка MX-записей домена с кэшированием результатов.

Реализует IMxChecker. Потокобезопасен через asyncio.Lock.
"""
from __future__ import annotations

import asyncio
import logging

import dns.resolver

from ..core.interfaces import IMxChecker

log = logging.getLogger(__name__)


class MxChecker(IMxChecker):
    """
    Асинхронная проверка MX-записей с in-memory кэшем.

    Пример использования::

        checker = MxChecker()
        ok = await checker.check("gmail.com")   # True
        ok = await checker.check("fake.invalid") # False
    """

    def __init__(self) -> None:
        self._cache: dict[str, bool] = {}
        self._lock = asyncio.Lock()

    async def check(self, domain: str) -> bool:
        """
        Вернуть True, если у домена есть хотя бы одна MX-запись.

        Результат кэшируется — повторные запросы к тому же домену
        не инициируют DNS-обращение.
        """
        domain = domain.lower().strip()
        if domain in self._cache:
            return self._cache[domain]

        async with self._lock:
            # double-checked locking
            if domain in self._cache:
                return self._cache[domain]

            result = await self._resolve(domain)
            self._cache[domain] = result
            return result

    @staticmethod
    async def _resolve(domain: str) -> bool:
        """Выполнить DNS-запрос в executor, чтобы не блокировать event loop."""
        try:
            loop = asyncio.get_event_loop()
            answers = await loop.run_in_executor(
                None, dns.resolver.resolve, domain, "MX"
            )
            return len(answers) > 0
        except Exception:
            return False

    @property
    def cache_size(self) -> int:
        """Количество закэшированных доменов."""
        return len(self._cache)

    def clear_cache(self) -> None:
        """Сбросить кэш (полезно в тестах)."""
        self._cache.clear()
