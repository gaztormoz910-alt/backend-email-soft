"""
services/hibp_scanner.py
Сканер Have I Been Pwned API.
Примечание: HIBP API не позволяет "искать" почты по доменам (он требует точный email для проверки).
Поэтому данный класс используется как шаблон или инструмент для верификации.
"""
from __future__ import annotations

import asyncio
import logging

from ..core.entities import Contact
from ..infrastructure.http_client import AsyncHttpClient

log = logging.getLogger(__name__)

class HIBPScanner:
    """
    Шаблон для интеграции с Have I Been Pwned.
    HIBP используется для проверки (верификации) существующих баз, а не для парсинга,
    так как API не отдает списки email по доменам.
    """
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def scan(self, client: AsyncHttpClient) -> list[Contact]:
        if not self._api_key:
            log.warning("⚠ HIBP_API_KEY не задан — HIBP пропущен.")
            return []

        log.info("ℹ HIBP: API Have I Been Pwned не поддерживает массовый поиск. Функция работает в режиме верификации.")
        # Поскольку из HIBP нельзя выгружать списки email, возвращаем пустой список
        # (Или здесь можно было бы написать код проверки уже собранных адресов)
        return []
