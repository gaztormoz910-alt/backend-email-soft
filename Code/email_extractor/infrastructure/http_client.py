"""
infrastructure/http_client.py
Обёртка над httpx.AsyncClient с поддержкой:
  - Случайного User-Agent (fake_useragent)
  - Автоматической декомпрессии gzip
  - Ограничения размера ответа
  - Подавления SSL-ошибок (verify=False)

Реализует IHttpClient.
"""
from __future__ import annotations

import gzip
import logging
from typing import AsyncGenerator

import httpx
from fake_useragent import UserAgent

from ..core.interfaces import IHttpClient

log = logging.getLogger(__name__)

# Один экземпляр на весь процесс — инициализация может занять время
_ua = UserAgent(
    browsers=["chrome", "firefox", "edge"],
    os=["windows", "macos", "linux"],
    fallback="chrome",
)


class AsyncHttpClient(IHttpClient):
    """
    Асинхронный HTTP-клиент на базе httpx.

    Должен использоваться как контекстный менеджер::

        async with AsyncHttpClient(timeout=10, max_mb=20) as client:
            data = await client.fetch("https://example.com")

    Attributes:
        timeout:    Таймаут запроса в секундах.
        max_mb:     Максимальный размер тела ответа (МБ). Больше — игнорируем.
        max_connections: Верхняя граница пула соединений.
    """

    def __init__(
        self,
        timeout: int = 10,
        max_mb: int = 20,
        max_connections: int = 60,
        max_keepalive: int = 20,
    ) -> None:
        self._timeout = timeout
        self._max_bytes = max_mb * 1024 * 1024
        limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive,
        )
        self._client = httpx.AsyncClient(
            timeout=timeout,
            limits=limits,
            follow_redirects=True,
            verify=False,
            http2=False,  # HTTP/2 hpack таблицы и буферы жрут RAM
        )

    # ------------------------------------------------------------------
    # IHttpClient implementation
    # ------------------------------------------------------------------

    async def fetch(self, url: str) -> bytes | None:
        """
        Выполнить GET-запрос с рандомным UA.

        Returns:
            Тело ответа (гарантированно распакованное если gzip),
            или ``None`` при любой ошибке.
        """
        try:
            headers = {"User-Agent": _ua.random}
            r = await self._client.get(url, headers=headers)
            if r.status_code != 200:
                return None
            content = r.content
            if len(content) > self._max_bytes:
                log.debug("⚠ Пропущен %s: размер %d МБ", url, len(content) // (1024 * 1024))
                return None
            # Распаковка gzip если сервер не сделал это автоматически
            if r.headers.get("content-encoding") == "gzip" or content[:2] == b"\x1f\x8b":
                try:
                    content = gzip.decompress(content)
                except Exception:
                    pass
            return content
        except Exception as exc:
            log.debug("✗ fetch(%s): %s", url[:80], exc)
            return None

    async def stream_lines(self, url: str) -> AsyncGenerator[str, None]:
        """
        Скачивает ответ потоково и выдает по одной строке (для O(1) памяти).
        Останавливает чтение, если превышен self._max_bytes.
        """
        try:
            headers = {"User-Agent": _ua.random}
            async with self._client.stream("GET", url, headers=headers) as r:
                if r.status_code != 200:
                    return
                bytes_read = 0
                async for line in r.aiter_lines():
                    # Приблизительная оценка прочитанных байт
                    bytes_read += len(line.encode('utf-8', errors='ignore'))
                    if bytes_read > self._max_bytes:
                        log.debug("⚠ Прервано потоковое чтение %s: превышен %d МБ", url, self._max_bytes // (1024 * 1024))
                        break
                    yield line
        except Exception as exc:
            log.debug("✗ stream_lines(%s): %s", url[:80], exc)

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "AsyncHttpClient":
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *args) -> None:
        await self._client.__aexit__(*args)

    async def aclose(self) -> None:
        """Явное закрытие клиента (альтернатива контекстному менеджеру)."""
        await self._client.aclose()
