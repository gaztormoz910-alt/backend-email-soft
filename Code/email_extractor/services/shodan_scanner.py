"""
services/shodan_scanner.py
Сканер Shodan API для поиска открытых баз данных и серверов, содержащих email-адреса.
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

def _check_stop() -> bool:
    """Проверить, запрошена ли остановка/отмена."""
    try:
        import email_extractor.cli.main as core_main
        return getattr(core_main, '_STOP_REQUESTED', False) or getattr(core_main, '_CANCEL_REQUESTED', False)
    except Exception:
        return False

class ShodanScanner:
    """
    Сканирует Shodan REST API на наличие открытых директорий и БД с email.
    """
    def __init__(self, api_key: str, queries: list[str]) -> None:
        self._api_key = api_key
        self._queries = queries
        self._api_url = "https://api.shodan.io/shodan/host/search"

    async def scan(self, client: AsyncHttpClient) -> list[Contact]:
        if not self._api_key:
            log.warning("⚠ SHODAN_API_KEY не задан — сканер Shodan пропущен.")
            return []

        contacts: dict[str, Contact] = {}
        
        for query in self._queries:
            if _check_stop():
                break
                
            log.debug(f"🔗 Shodan API: поиск по запросу '{query}'")
            encoded_query = urllib.parse.quote(query)
            url = f"{self._api_url}?key={self._api_key}&query={encoded_query}"
            
            try:
                raw = await client.fetch(url)
                if not raw:
                    await asyncio.sleep(1)
                    continue

                data = json.loads(raw)
                matches = data.get("matches", [])
                
                batch_added = 0
                for match in matches:
                    # Ищем email в сырых данных ответа сервера (баннерах)
                    data_str = match.get("data", "")
                    if not data_str:
                        continue
                        
                    found_emails = EMAIL_RE.findall(data_str)
                    for e in found_emails:
                        e_lower = e.lower()
                        if not is_fake_email(e_lower) and e_lower not in contacts:
                            contacts[e_lower] = Contact.from_email_only(e_lower)
                            batch_added += 1

                if batch_added > 0:
                    msg = f"   🎯 Shodan [{query}]: +{batch_added} email"
                    log.info(msg)
                    try:
                        import email_extractor.cli.main as core_main
                        ws = getattr(core_main, 'ws_manager', None)
                        if ws:
                            asyncio.create_task(ws.send_log(msg))
                    except Exception:
                        pass
                        
            except Exception as exc:
                log.debug("Shodan API ошибка для %s: %s", query, exc)
                
            await asyncio.sleep(1) # Rate limit Shodan: 1 request per second

        log.info("📡 Shodan: найдено %d уникальных email", len(contacts))
        return list(contacts.values())
