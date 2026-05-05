"""
services/grayhat_scanner.py
Сканер GrayHatWarfare API для поиска открытых Amazon S3 корзин (buckets) с CSV файлами.
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
    try:
        import email_extractor.cli.main as core_main
        return getattr(core_main, '_STOP_REQUESTED', False) or getattr(core_main, '_CANCEL_REQUESTED', False)
    except Exception:
        return False

class GrayhatScanner:
    """
    Ищет файлы контактов (CSV, TXT) в публичных S3 buckets, скачивает их и парсит почты.
    """
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._api_url = "https://buckets.grayhatwarfare.com/api/v1/files"

    async def scan(self, client: AsyncHttpClient) -> list[Contact]:
        if not self._api_key:
            log.warning("⚠ GRAYHAT_API_KEY не задан — сканер S3 Buckets пропущен.")
            return []

        contacts: dict[str, Contact] = {}
        keywords = ["contacts.csv", "subscribers.csv", "users.json", "emails.txt"]
        
        for keyword in keywords:
            if _check_stop():
                break
                
            log.debug(f"🔗 GrayHat API: поиск S3 файлов '{keyword}'")
            encoded_kw = urllib.parse.quote(keyword)
            url = f"{self._api_url}/{encoded_kw}?access_token={self._api_key}"
            
            try:
                raw = await client.fetch(url)
                if not raw:
                    await asyncio.sleep(1)
                    continue

                data = json.loads(raw)
                files = data.get("files", [])
                
                batch_added = 0
                for file_info in files:
                    if _check_stop():
                        break
                        
                    file_url = file_info.get("url")
                    if not file_url:
                        continue
                        
                    # Скачиваем сам файл из корзины S3
                    try:
                        file_content = await client.fetch(file_url)
                        if file_content:
                            found_emails = EMAIL_RE.findall(file_content)
                            for e in found_emails:
                                e_lower = e.lower()
                                if not is_fake_email(e_lower) and e_lower not in contacts:
                                    contacts[e_lower] = Contact.from_email_only(e_lower)
                                    batch_added += 1
                    except Exception:
                        pass
                        
                if batch_added > 0:
                    msg = f"   🎯 GrayHat S3 [{keyword}]: +{batch_added} email"
                    log.info(msg)
                    try:
                        import email_extractor.cli.main as core_main
                        ws = getattr(core_main, 'ws_manager', None)
                        if ws:
                            asyncio.create_task(ws.send_log(msg))
                    except Exception:
                        pass
                        
            except Exception as exc:
                log.debug("GrayHat API ошибка для %s: %s", keyword, exc)
                
            await asyncio.sleep(1)

        log.info("☁ GrayHat S3: найдено %d уникальных email", len(contacts))
        return list(contacts.values())
