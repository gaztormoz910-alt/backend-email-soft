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


from config import OUTPUT_DIR

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

    def _get_prefixes(self) -> list[str]:
        """Загрузить префиксы из файла или использовать встроенный список топ-слов."""
        dict_path = OUTPUT_DIR / "comb_dict.txt"
        if dict_path.exists():
            try:
                with open(dict_path, "r", encoding="utf-8") as f:
                    lines = [line.strip().lower() for line in f if line.strip()]
                if lines:
                    if "" not in lines:
                        lines.insert(0, "")  # Всегда добавляем пустой префикс (чистый домен)
                    return lines
            except Exception as e:
                log.error("Failed to read comb_dict.txt: %s", e)
        
        # Топ-список по умолчанию, если файла нет
        return [
            "", # Пустая строка для поиска просто по @domain.com
            "alex", "john", "anna", "maria", "mike", "chris", "david", "sarah", "paul", "mark",
            "ivan", "sergey", "dmitry", "andrey", "elena", "natalia", "olga", "igor", "vlad", "maxim",
            "admin", "info", "support", "contact", "sales", "office", "hello", "team", "manager",
            "test", "user", "mail", "web", "pro", "master", "dev", "app", "system"
        ]

    from typing import AsyncGenerator

    async def scan(self, client: AsyncHttpClient) -> AsyncGenerator[Contact, None]:
        seen_emails: set[str] = set()
        prefixes = self._get_prefixes()
        total_queries = len(self._domains) * len(prefixes)
        current_query = 0

        for domain in self._domains:
            for prefix in prefixes:
                if _check_stop():
                    return list(contacts.values())
                current_query += 1
                try:
                    query = f"{prefix}@{domain}"
                    url = f"{self._api_url}?query={urllib.parse.quote(query)}"
                    
                    if current_query % 5 == 0 or prefix == "":
                        log.debug("🔗 COMB API [%d/%d]: %s", current_query, total_queries, query)

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
                        m = EMAIL_RE.search(line)
                        if m:
                            e = m.group(0).lower()
                            if not is_fake_email(e) and e not in seen_emails:
                                seen_emails.add(e)
                                yield Contact.from_email_only(e)
                                batch_added += 1
                                
                                # Защита от переполнения RAM: чистим локальный кэш
                                if len(seen_emails) > 500000:
                                    seen_emails.clear()

                    if batch_added > 0:
                        msg = f"   🎯 COMB {query}: +{batch_added} email"
                        log.info(msg)
                        # Отправка лога во фронтенд
                        try:
                            import email_extractor.cli.main as core_main
                            ws = getattr(core_main, 'ws_manager', None)
                            if ws:
                                asyncio.create_task(ws.send_log(msg))
                        except Exception:
                            pass

                except Exception as exc:
                    log.debug("COMB API ошибка для %s: %s", domain, exc)

                await asyncio.sleep(self._sleep)

        log.info("🔓 COMB API: завершено сканирование %d запросов", total_queries)
