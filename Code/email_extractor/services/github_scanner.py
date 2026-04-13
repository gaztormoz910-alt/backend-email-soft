"""
services/github_scanner.py
Сканирование открытых GitHub-репозиториев: извлечение email из истории коммитов.

Реализует IGitHubScanner.
Требует GITHUB_TOKEN — без него возвращает пустой список.
"""
from __future__ import annotations

import asyncio
import json
import logging

from ..core.entities import Contact
from ..core.interfaces import IGitHubScanner, IHttpClient
from ..services.email_extractor import is_fake_email, split_name

log = logging.getLogger(__name__)

_NOREPLY_SUFFIX = "@users.noreply.github.com"

# Поисковые запросы для поиска репозиториев
_REPO_QUERIES = ["stars:>1000", "email list"]


class GitHubScanner(IGitHubScanner):
    """
    Сканирует публичные репозитории GitHub через REST API v3.

    Алгоритм:
      1. Найти репозитории по поисковым запросам.
      2. Для каждого репозитория получить список последних коммитов.
      3. Извлечь email автора из метаданных коммита.
      4. Отфильтровать noreply-адреса и фейки.

    Args:
        token:      GitHub Personal Access Token.
        pages:      Сколько страниц репозиториев брать на каждый запрос (макс. 3).
        per_page:   Репозиториев / коммитов на страницу.
    """

    def __init__(
        self,
        token: str,
        pages: int = 3,
        per_page: int = 30,
    ) -> None:
        self._token = token
        self._pages = pages
        self._per_page = per_page

    # ------------------------------------------------------------------
    # IGitHubScanner implementation
    # ------------------------------------------------------------------

    async def scan(self, client: IHttpClient) -> list[Contact]:
        if not self._token:
            log.warning("⚠ GITHUB_TOKEN не задан — модуль GitHub пропущен.")
            return []

        contacts: list[Contact] = []

        for query in _REPO_QUERIES:
            for page in range(1, self._pages + 1):
                repos = await self._search_repos(client, query, page)
                if not repos:
                    break

                tasks = [self._scan_repo(client, repo) for repo in repos]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for result in results:
                    if isinstance(result, list):
                        contacts.extend(result)

        # Дедупликация по email
        seen: dict[str, Contact] = {}
        for c in contacts:
            if c.email not in seen:
                seen[c.email] = c

        log.info("🐙 GitHub: найдено %d уникальных контактов", len(seen))
        return list(seen.values())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"token {self._token}",
            "Accept": "application/vnd.github.v3+json",
        }

    async def _api_get(self, client: IHttpClient, url: str, params: dict | None = None) -> dict | list | None:
        """Выполнить GET к GitHub API; вернуть распарсенный JSON или None."""
        import urllib.parse

        full_url = url
        if params:
            full_url = f"{url}?{urllib.parse.urlencode(params)}"

        # Нам нужен raw fetch с заголовками — используем httpx напрямую через client
        # Но IHttpClient не поддерживает кастомные заголовки.
        # Поэтому делаем запрос через httpx напрямую (GitHub требует Authorization).
        try:
            import httpx

            async with httpx.AsyncClient(
                headers=self._headers,
                timeout=15,
                follow_redirects=True,
            ) as hx:
                r = await hx.get(full_url)
                if r.status_code != 200:
                    return None
                return r.json()
        except Exception as exc:
            log.debug("GitHub API error (%s): %s", url, exc)
            return None

    async def _search_repos(self, client: IHttpClient, query: str, page: int) -> list[dict]:
        data = await self._api_get(
            client,
            "https://api.github.com/search/repositories",
            {"q": query, "per_page": self._per_page, "page": page},
        )
        if isinstance(data, dict):
            return data.get("items", [])
        return []

    async def _scan_repo(self, client: IHttpClient, repo: dict) -> list[Contact]:
        commits_url = repo.get("commits_url", "").replace("{/sha}", "")
        data = await self._api_get(
            client,
            commits_url,
            {"per_page": self._per_page},
        )
        if not isinstance(data, list):
            return []

        contacts: list[Contact] = []
        for commit in data:
            author = commit.get("commit", {}).get("author", {})
            email: str = author.get("email", "").lower().strip()
            if not email:
                continue
            if email.endswith(_NOREPLY_SUFFIX):
                continue
            if is_fake_email(email):
                continue
            first, last = split_name(author.get("name", ""))
            contacts.append(Contact(email=email, first_name=first, last_name=last))

        await asyncio.sleep(0.5)
        return contacts
