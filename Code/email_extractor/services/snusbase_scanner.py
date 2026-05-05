"""
services/snusbase_scanner.py
Сканер Snusbase API для поиска свежих утекших данных по доменам.
"""
from __future__ import annotations

import asyncio
import json
import logging

from ..core.entities import Contact
from ..infrastructure.http_client import AsyncHttpClient
from .email_extractor import is_fake_email

log = logging.getLogger(__name__)

def _check_stop() -> bool:
    try:
        import email_extractor.cli.main as core_main
        return getattr(core_main, '_STOP_REQUESTED', False) or getattr(core_main, '_CANCEL_REQUESTED', False)
    except Exception:
        return False

class SnusbaseScanner:
    """
    Сканирует Snusbase API.
    """
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._api_url = "https://api.snusbase.com/data/search"

    async def scan(self, client: AsyncHttpClient) -> list[Contact]:
        if not self._api_key:
            log.warning("⚠ SNUSBASE_API_KEY не задан — сканер пропущен.")
            return []

        contacts: dict[str, Contact] = {}
        domains = ["gmail.com", "yahoo.com", "hotmail.com"]
        
        headers = {
            "Auth": self._api_key,
            "Content-Type": "application/json"
        }

        import httpx
        async with httpx.AsyncClient(headers=headers, timeout=15) as hx:
            for domain in domains:
                if _check_stop():
                    break
                    
                log.debug(f"🔗 Snusbase API: поиск по домену {domain}")
                
                payload = {
                    "terms": [domain],
                    "types": ["email"],
                    "wildcard": True
                }
                
                try:
                    r = await hx.post(self._api_url, json=payload)
                    if r.status_code != 200:
                        await asyncio.sleep(1)
                        continue

                    data = r.json()
                    results = data.get("results", {})
                    
                    batch_added = 0
                    for db_name, entries in results.items():
                        for entry in entries:
                            if isinstance(entry, dict) and "email" in entry:
                                e = str(entry["email"]).lower().strip()
                                if not is_fake_email(e) and e not in contacts:
                                    contacts[e] = Contact.from_email_only(e)
                                    batch_added += 1

                    if batch_added > 0:
                        msg = f"   🎯 Snusbase [{domain}]: +{batch_added} email"
                        log.info(msg)
                        try:
                            import email_extractor.cli.main as core_main
                            ws = getattr(core_main, 'ws_manager', None)
                            if ws:
                                asyncio.create_task(ws.send_log(msg))
                        except Exception:
                            pass
                            
                except Exception as exc:
                    log.debug("Snusbase API ошибка: %s", exc)
                    
                await asyncio.sleep(2) # Защита от rate limit

        log.info("🗄 Snusbase: найдено %d уникальных email", len(contacts))
        return list(contacts.values())
