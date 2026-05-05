"""
services/email_extractor.py
Основная логика извлечения email-адресов из текстового и бинарного контента.

Реализует IEmailExtractorService.
Не зависит ни от httpx, ни от файловой системы — только regex + stdlib.
"""
from __future__ import annotations

from typing import AsyncGenerator
import csv
import io
import re

from ..core.entities import Contact
from ..core.interfaces import IEmailExtractorService
from .disposable_domains import DISPOSABLE_DOMAINS, ROLE_PREFIXES

# ---------------------------------------------------------------------------
# Константы фильтрации
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r'\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b')

# Один скомпилированный regex вместо 11 отдельных — в ~5-10x быстрее
_FAKE_LOCAL_RE = re.compile(
    r'^(?:'
    r'random_\d+'
    r'|email\d+'
    r'|first\d+'
    r'|sample\d*'
    r'|noreply'
    r'|no-reply'
    r'|donotreply'
    r'|postmaster'
    r'|mailer-daemon'
    r'|bounce'
    r')@',
    re.I,
)

_IGNORED_EMAILS: frozenset[str] = frozenset({
    "email@example.com", "test@test.com", "user@example.com",
    "no-reply@example.com", "admin@example.com", "example@example.com",
    "noreply@example.com", "info@example.com", "mail@example.com",
    "support@example.com", "contact@example.com", "user@test.com",
    "email@domain.com", "yourname@domain.com", "name@example.com",
    "email@email.com",
})

_FAKE_DOMAINS: frozenset[str] = frozenset({
    "example.com", "test.com", "domain.com", "localhost.com",
    "email.com", "placeholder.com", "mailinator.com",
}) | DISPOSABLE_DOMAINS  # + 300+ одноразовых доменов из blocklist

# ---------------------------------------------------------------------------
# Whitelist доменов крупных провайдеров — MX-проверка для них не нужна,
# экономит тысячи DNS-запросов и ускоряет пайплайн в разы
# ---------------------------------------------------------------------------
TRUSTED_DOMAINS: frozenset[str] = frozenset({
    # Google
    "gmail.com", "googlemail.com",
    # Microsoft
    "outlook.com", "hotmail.com", "live.com", "msn.com",
    "hotmail.co.uk", "hotmail.fr", "hotmail.de", "hotmail.it",
    "outlook.co.uk", "outlook.fr", "outlook.de",
    # Yahoo
    "yahoo.com", "ymail.com", "rocketmail.com",
    "yahoo.co.uk", "yahoo.fr", "yahoo.de", "yahoo.co.jp",
    # Apple
    "icloud.com", "me.com", "mac.com",
    # AOL
    "aol.com",
    # ProtonMail
    "protonmail.com", "proton.me", "pm.me",
    # Европа
    "gmx.com", "gmx.de", "gmx.net", "gmx.at",
    "web.de", "t-online.de",
    "orange.fr", "laposte.net", "free.fr", "sfr.fr",
    "libero.it", "virgilio.it",
    "wp.pl", "onet.pl", "interia.pl", "o2.pl",
    "seznam.cz", "abv.bg",
    # СНГ
    "mail.ru", "bk.ru", "inbox.ru", "list.ru",
    "yandex.ru", "yandex.com", "ya.ru",
    "rambler.ru",
    "ukr.net", "i.ua", "meta.ua",
    # США ISP
    "comcast.net", "verizon.net", "att.net",
    "sbcglobal.net", "cox.net", "charter.net",
    # Прочие
    "zoho.com", "fastmail.com",
    "tutanota.com", "tuta.io", "mail.com",
})


# ---------------------------------------------------------------------------
# Публичные вспомогательные функции (используются в других модулях)
# ---------------------------------------------------------------------------

def is_fake_email(email: str) -> bool:
    """Вернуть True, если email выглядит как заглушка / тестовый / ролевой адрес."""
    e = email.lower().strip()
    if e in _IGNORED_EMAILS or len(e) < 6 or "@" not in e:
        return True
    local, _, domain = e.partition("@")
    if len(domain) < 3:
        return True
    if domain in _FAKE_DOMAINS:
        return True
    # Ролевые/технические адреса (info@, admin@, support@ и т.д.)
    if local in ROLE_PREFIXES:
        return True
    # Один regex вместо цикла из 11 — быстрее в ~5-10 раз
    if _FAKE_LOCAL_RE.match(e):
        return True
    return False


def split_name(full: str) -> tuple[str | None, str | None]:
    """Разбить полное имя на (first, last). При неудаче вернуть (None, None)."""
    full = re.sub(r'["""\'\(\)\[\]]', '', full).strip()
    if not full or len(full) > 60 or any(c in full for c in '@<>{}'):
        return None, None
    parts = full.split()
    first = parts[0].capitalize()
    last = parts[-1].capitalize() if len(parts) > 1 else None
    return first, last


# ---------------------------------------------------------------------------
# Внутренние парсеры
# ---------------------------------------------------------------------------

def _decode(raw: bytes) -> str:
    """Попробовать несколько кодировок, вернуть строку."""
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            pass
    return raw.decode("utf-8", errors="replace")


def _parse_csv_auto(text: str) -> list[Contact]:
    """
    Умный парсер CSV: ищет колонки email/first/last по заголовкам.
    Если заголовков нет — фолбэк на regex-поиск по всем ячейкам.
    """
    results: dict[str, Contact] = {}

    try:
        dialect = csv.Sniffer().sniff(text[:1024])
        reader = csv.reader(io.StringIO(text), dialect)
    except Exception:
        reader = csv.reader(io.StringIO(text), delimiter=",")

    headers: list[str] = []

    def col_index(keywords: list[str]) -> int | None:
        for kw in keywords:
            for i, h in enumerate(headers):
                if kw in h:
                    return i
        return None

    for row_num, row in enumerate(reader):
        if not any(row):
            continue
        if row_num == 0:
            headers = [h.lower().strip() for h in row]
            continue

        ec = col_index(["email", "e-mail", "mail"])
        if ec is None:
            # Нет заголовка email — ищем regex по всем ячейкам
            for cell in row:
                m = EMAIL_RE.search(cell)
                if m:
                    e = m.group(0).lower()
                    if not is_fake_email(e):
                        results[e] = Contact.from_email_only(e)
            continue

        if ec >= len(row):
            continue

        m = EMAIL_RE.search(row[ec])
        if not m:
            continue

        e = m.group(0).lower()
        if is_fake_email(e):
            continue

        fc = col_index(["first", "fname"])
        lc = col_index(["last", "lname"])
        first = row[fc].strip() if fc is not None and fc < len(row) else ""
        last = row[lc].strip() if lc is not None and lc < len(row) else ""
        results[e] = Contact.from_tuple(first, last, e)

    return list(results.values())


def _parse_generic(text: str) -> list[Contact]:
    """Regex-поиск email по всему тексту без учёта структуры."""
    seen: dict[str, Contact] = {}
    for email in EMAIL_RE.findall(text):
        e = email.lower()
        if not is_fake_email(e) and e not in seen:
            seen[e] = Contact.from_email_only(e)
    return list(seen.values())


# ---------------------------------------------------------------------------
# Реализация интерфейса
# ---------------------------------------------------------------------------

class EmailExtractorService(IEmailExtractorService):
    """
    Сервис извлечения контактов из текста и HTTP-контента.

    Не хранит состояния — все методы stateless.
    """

    def extract_from_text(self, text: str) -> list[Contact]:
        """Найти все email в произвольном тексте (regex-поиск)."""
        return _parse_generic(text)

    def extract_from_url_content(self, raw: bytes, url: str) -> list[Contact]:
        """
        Выбрать стратегию парсинга по URL:
          - mc4wp / mailpoet → plaintext regex
          - *.csv / export=true / format=csv → CSV-парсер
          - всё остальное → plaintext regex
        """
        low = url.lower()
        text = _decode(raw)

        if low.endswith(".csv") or "export=true" in low or "format=csv" in low:
            return _parse_csv_auto(text)

        return _parse_generic(text)

    async def extract_from_stream(self, stream: AsyncGenerator[str, None], url: str) -> list[Contact]:
        """Оптимизированный построчный поиск с минимальным расходом ОЗУ."""
        low = url.lower()
        is_csv = low.endswith(".csv") or "export=true" in low or "format=csv" in low
        seen: dict[str, Contact] = {}

        if is_csv:
            headers = []
            ec, fc, lc = None, None, None
            row_num = 0

            async for line in stream:
                try:
                    # Быстрый парсер одной строки. Если строка обрезана кавычкой, мы просто попробуем вытащить email регуляркой ниже
                    row = next(csv.reader([line]))
                except Exception:
                    # Фолбэк на регулярку если csv.reader упал
                    for email in EMAIL_RE.findall(line):
                        e = email.lower()
                        if not is_fake_email(e) and e not in seen:
                            seen[e] = Contact.from_email_only(e)
                    continue

                if not any(row):
                    continue

                if row_num == 0:
                    headers = [h.lower().strip() for h in row]
                    def col_index(keywords: list[str]) -> int | None:
                        for kw in keywords:
                            for i, h in enumerate(headers):
                                if kw in h: return i
                        return None
                    ec = col_index(["email", "e-mail", "mail"])
                    fc = col_index(["first", "fname"])
                    lc = col_index(["last", "lname"])
                    row_num += 1
                    continue

                if ec is None:
                    # Ищем во всех колонках
                    for cell in row:
                        m = EMAIL_RE.search(cell)
                        if m:
                            e = m.group(0).lower()
                            if not is_fake_email(e) and e not in seen:
                                seen[e] = Contact.from_email_only(e)
                else:
                    if ec < len(row):
                        m = EMAIL_RE.search(row[ec])
                        if m:
                            e = m.group(0).lower()
                            if not is_fake_email(e) and e not in seen:
                                first = row[fc].strip() if fc is not None and fc < len(row) else ""
                                last = row[lc].strip() if lc is not None and lc < len(row) else ""
                                seen[e] = Contact.from_tuple(first, last, e)
                row_num += 1
        else:
            # Обычный потоковый поиск через regex (HTML, TXT, JSON)
            async for line in stream:
                for email in EMAIL_RE.findall(line):
                    e = email.lower()
                    if not is_fake_email(e) and e not in seen:
                        seen[e] = Contact.from_email_only(e)

        return list(seen.values())
