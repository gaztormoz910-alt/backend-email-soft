"""
services/comb_scanner.py
Сканер бесплатного ProxyNova COMB API (Combination Of Many Breaches).

Запрашивает email по домену: ?query=@gmail.com → получает строки email:password.
Из строк извлекаются только email-адреса.

API не требует ключа и является публичным.
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse

from ..core.entities import Contact
from ..infrastructure.http_client import AsyncHttpClient
from .email_extractor import EMAIL_RE, is_fake_email

log = logging.getLogger(__name__)

# Импортируем флаги остановки из cli.main (ленивый импорт в методе)
def _check_stop() -> bool:
    """Проверить, запрошена ли остановка/отмена."""
    try:
        import email_extractor.cli.main as core_main
        return getattr(core_main, '_STOP_REQUESTED', False) or getattr(core_main, '_CANCEL_REQUESTED', False)
    except Exception:
        return False


class CombApiScanner:
    """
    Сканирует бесплатный ProxyNova COMB API.
    Запрашивает email по домену: ?query=@gmail.com → получает строки email:password.
    Из строк извлекаются только email.
    """

    def __init__(self, api_url: str, domains: list[str], sleep_between: float = 3.0) -> None:
        self._api_url = api_url
        self._domains = domains
        self._sleep = sleep_between

    async def scan(self, client: AsyncHttpClient) -> list[Contact]:
        contacts: dict[str, Contact] = {}
        total_queries = len(self._domains)

        for idx, domain in enumerate(self._domains, 1):
            if _check_stop():
                break
            try:
                query = f"@{domain}"
                url = f"{self._api_url}?query={urllib.parse.quote(query)}"
                log.debug("🔗 COMB API [%d/%d]: %s", idx, total_queries, query)

                raw = await client.fetch(url)
                if not raw:
                    await asyncio.sleep(self._sleep)
                    continue

                try:
                    data = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    await asyncio.sleep(self._sleep)
                    continue

                lines = data.get("lines", [])
                if not lines:
                    await asyncio.sleep(self._sleep)
                    continue

                batch_added = 0
                for line in lines:
                    if not isinstance(line, str):
                        continue
                    # Строка формата: email:password или email;password
                    m = EMAIL_RE.search(line)
                    if m:
                        e = m.group(0).lower()
                        if not is_fake_email(e) and e not in contacts:
                            contacts[e] = Contact.from_email_only(e)
                            batch_added += 1

                if batch_added > 0:
                    log.info("   🎯 COMB @%s: +%d email", domain, batch_added)

            except Exception as exc:
                log.debug("COMB API ошибка для %s: %s", domain, exc)

            await asyncio.sleep(self._sleep)

        log.info("🔓 COMB API: найдено %d уникальных email из %d доменов", len(contacts), total_queries)
        return list(contacts.values())
