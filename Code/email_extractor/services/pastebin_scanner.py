"""
services/pastebin_scanner.py
Сканер официального Scraping API Pastebin для перехвата свежих дампов.
"""
from __future__ import annotations

import asyncio
import json
import logging

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

class PastebinScanner:
    """
    Скрапит свежие пасты через Pastebin Scraping API.
    Требует белый IP в настройках аккаунта Pastebin PRO.
    """
    def __init__(self, api_url: str) -> None:
        self._api_url = api_url

    async def scan(self, client: AsyncHttpClient) -> list[Contact]:
        contacts: dict[str, Contact] = {}
        
        log.debug("🔗 Pastebin: Запрашиваем список свежих паст")
        try:
            url = f"{self._api_url}?limit=100"
            raw = await client.fetch(url)
            if not raw:
                log.warning("⚠ Pastebin API не ответил. Возможно, IP не в белом списке.")
                return []

            try:
                pastes = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("⚠ Pastebin API вернул не JSON. Проверьте права доступа.")
                return []

            batch_added = 0
            for paste in pastes:
                if _check_stop():
                    break
                    
                paste_key = paste.get("key")
                if not paste_key:
                    continue
                    
                # Получаем содержимое конкретной пасты
                content_url = f"https://scrape.pastebin.com/api_scrape_item.php?i={paste_key}"
                paste_content = await client.fetch(content_url)
                
                if paste_content:
                    found_emails = EMAIL_RE.findall(paste_content)
                    for e in found_emails:
                        e_lower = e.lower()
                        if not is_fake_email(e_lower) and e_lower not in contacts:
                            contacts[e_lower] = Contact.from_email_only(e_lower)
                            batch_added += 1
                            
                await asyncio.sleep(0.5) # Лимит Scraping API

            if batch_added > 0:
                msg = f"   🎯 Pastebin: вытянуто +{batch_added} email из свежих паст"
                log.info(msg)
                try:
                    import email_extractor.cli.main as core_main
                    ws = getattr(core_main, 'ws_manager', None)
                    if ws:
                        asyncio.create_task(ws.send_log(msg))
                except Exception:
                    pass
                        
        except Exception as exc:
            log.debug("Pastebin API ошибка: %s", exc)

        log.info("📝 Pastebin: найдено %d уникальных email", len(contacts))
        return list(contacts.values())
