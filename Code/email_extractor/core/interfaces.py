"""
core/interfaces.py
Абстрактные интерфейсы (порты) — описывают контракты, не реализации.
Implementations живут в infrastructure/ и services/.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import AsyncGenerator

from .entities import Contact


# ---------------------------------------------------------------------------
# HTTP / Network port
# ---------------------------------------------------------------------------

class IHttpClient(ABC):
    """Абстракция над HTTP-клиентом."""

    @abstractmethod
    async def fetch(self, url: str) -> bytes | None:
        """
        Выполнить GET-запрос к *url*.

        Returns:
            Тело ответа в байтах, или ``None`` при ошибке / нежелательном статусе.
        """


# ---------------------------------------------------------------------------
# Repository port (persistence)
# ---------------------------------------------------------------------------

class IContactRepository(ABC):
    """Хранилище контактов (чтение / запись)."""

    @abstractmethod
    def load(self) -> dict[str, Contact]:
        """
        Загрузить ранее сохранённые контакты.

        Returns:
            Словарь ``{email: Contact}``.
        """

    @abstractmethod
    def save(self, contacts: dict[str, Contact]) -> None:
        """
        Записать контакты на диск (перезапись).

        Args:
            contacts: Словарь ``{email: Contact}``.
        """


# ---------------------------------------------------------------------------
# Email extraction / parsing port
# ---------------------------------------------------------------------------

class IEmailExtractorService(ABC):
    """Сервис извлечения email-адресов из сырого контента."""

    @abstractmethod
    def extract_from_text(self, text: str) -> list[Contact]:
        """
        Найти email-адреса в произвольном тексте.

        Args:
            text: Сырой текст (HTML, plaintext, CSV …).

        Returns:
            Список найденных контактов.
        """

    @abstractmethod
    def extract_from_url_content(self, raw: bytes, url: str) -> list[Contact]:
        """
        Найти email-адреса в теле HTTP-ответа с учётом типа URL.

        Args:
            raw: Тело ответа в байтах.
            url: URL источника (используется для определения формата).

        Returns:
            Список найденных контактов.
        """


# ---------------------------------------------------------------------------
# Discovery ports (crawler / scanner)
# ---------------------------------------------------------------------------

class IPipermailCrawler(ABC):
    """Обход Pipermail-серверов с возвратом URL для последующей обработки."""

    @abstractmethod
    def discover(self, client: IHttpClient) -> AsyncGenerator[str, None]:
        """Асинхронный генератор URL страниц Pipermail."""


class ISearchDiscovery(ABC):
    """Поиск URL через поисковые дорки (Google, Bing и т.д.)."""

    @abstractmethod
    async def discover(
        self,
        client: IHttpClient,
        known_urls: set[str],
    ) -> list[str]:
        """
        Вернуть список новых URL, найденных через дорки.

        Args:
            client:     HTTP-клиент для Bing-подзапросов.
            known_urls: Уже известные URL (не возвращать повторно).
        """


class IGitHubScanner(ABC):
    """Сканирование GitHub-репозиториев в поиске email из коммитов."""

    @abstractmethod
    async def scan(self, client: IHttpClient) -> list[Contact]:
        """Вернуть список контактов, найденных в истории коммитов."""


class ILocalFileScanner(ABC):
    """Рекурсивное сканирование локальной файловой системы."""

    @abstractmethod
    def scan(self, directory) -> list[Contact]:
        """
        Рекурсивно обойти *directory* и вернуть все найденные контакты.

        Args:
            directory: ``pathlib.Path`` к директории.
        """


# ---------------------------------------------------------------------------
# MX-verification port
# ---------------------------------------------------------------------------

class IMxChecker(ABC):
    """Проверка наличия MX-записей у домена."""

    @abstractmethod
    async def check(self, domain: str) -> bool:
        """
        Проверить MX-записи для *domain*.

        Returns:
            ``True``, если MX-запись существует; ``False`` иначе.
        """
