"""
services/mx_checker.py
Проверка MX-записей домена с кэшированием результатов (LRU, лимит 5000).

Реализует IMxChecker. Потокобезопасен через asyncio.Lock.
"""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict

import dns.resolver

from ..core.interfaces import IMxChecker

log = logging.getLogger(__name__)

_MX_CACHE_LIMIT = 5_000  # максимум уникальных доменов в кэше


class MxChecker(IMxChecker):
    """
    Асинхронная проверка MX-записей с LRU in-memory кэшем.

    Кэш ограничен _MX_CACHE_LIMIT записями. При превышении вытесняются
    самые давно использованные домены (least-recently-used), что не даёт
    словарю расти бесконечно и съедать RAM.

    Пример использования::

        checker = MxChecker()
        ok = await checker.check("gmail.com")   # True
        ok = await checker.check("fake.invalid") # False
    """

    def __init__(self) -> None:
        # OrderedDict: порядок = порядок последнего использования
        self._cache: OrderedDict[str, bool] = OrderedDict()
        self._lock = asyncio.Lock()

    async def check(self, domain: str) -> bool:
        """
        Вернуть True, если у домена есть хотя бы одна MX-запись.

        Результат кэшируется (LRU, лимит 5000). Повторные запросы к тому же
        домену не инициируют DNS-обращение.
        """
        domain = domain.lower().strip()

        async with self._lock:
            if domain in self._cache:
                # Обновляем позицию (move_to_end = «только что использован»)
                self._cache.move_to_end(domain)
                return self._cache[domain]

            result = await self._resolve(domain)

            # Вытеснение самого старого элемента при переполнении
            if len(self._cache) >= _MX_CACHE_LIMIT:
                self._cache.popitem(last=False)

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

