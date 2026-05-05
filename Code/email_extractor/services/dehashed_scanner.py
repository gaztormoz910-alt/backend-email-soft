"""
services/dehashed_scanner.py
Сканер DeHashed API для поиска свежих утечек (Weekly Datasets).
"""
from __future__ import annotations

import asyncio
import json
import logging
import base64
import urllib.parse

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

class DehashedScanner:
    """
    Сканирует Dehashed API.
    """
    def __init__(self, api_key: str, username: str) -> None:
        self._api_key = api_key
        self._username = username
        self._api_url = "https://api.dehashed.com/search"

    async def scan(self, client: AsyncHttpClient) -> list[Contact]:
        if not self._api_key or not self._username:
            log.warning("⚠ DEHASHED_API_KEY или DEHASHED_USERNAME не задан — сканер пропущен.")
            return []

        contacts: dict[str, Contact] = {}
        # Пример: ищем по самым популярным доменам
        domains = ["gmail.com", "yahoo.com", "hotmail.com"]
        
        # Для Dehashed нужна базовая авторизация
        auth_string = f"{self._username}:{self._api_key}"
        auth_bytes = auth_string.encode('ascii')
        base64_bytes = base64.b64encode(auth_bytes)
        base64_string = base64_bytes.decode('ascii')
        headers = {"Accept": "application/json", "Authorization": f"Basic {base64_string}"}

        import httpx
        async with httpx.AsyncClient(headers=headers, timeout=15) as hx:
            for domain in domains:
                if _check_stop():
                    break
                    
                log.debug(f"🔗 DeHashed API: поиск по домену {domain}")
                query = f"email:{domain}"
                url = f"{self._api_url}?query={urllib.parse.quote(query)}"
                
                try:
                    r = await hx.get(url)
                    if r.status_code != 200:
                        await asyncio.sleep(1)
                        continue

                    data = r.json()
                    entries = data.get("entries", [])
                    
                    batch_added = 0
                    for entry in entries:
                        if not isinstance(entry, dict):
                            continue
                        e = entry.get("email", "")
                        if e and isinstance(e, str):
                            e_lower = e.lower().strip()
                            if not is_fake_email(e_lower) and e_lower not in contacts:
                                contacts[e_lower] = Contact.from_email_only(e_lower)
                                batch_added += 1

                    if batch_added > 0:
                        msg = f"   🎯 DeHashed [{domain}]: +{batch_added} email"
                        log.info(msg)
                        try:
                            import email_extractor.cli.main as core_main
                            ws = getattr(core_main, 'ws_manager', None)
                            if ws:
                                asyncio.create_task(ws.send_log(msg))
                        except Exception:
                            pass
                            
                except Exception as exc:
                    log.debug("DeHashed API ошибка: %s", exc)
                    
                await asyncio.sleep(1)

        log.info("🗄 DeHashed: найдено %d уникальных email", len(contacts))
        return list(contacts.values())
