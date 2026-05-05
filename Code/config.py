"""
config.py
Единственный источник конфигурации для всего пакета.

Читает значения из переменных окружения (через python-dotenv если установлен,
иначе из os.environ). Все остальные модули импортируют настройки отсюда,
а не из os.environ напрямую.
"""
from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Автозагрузка .env (если python-dotenv установлен)
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv

    # Ищем .env в корне проекта (на уровень выше, чем Code/)
    _env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(dotenv_path=_env_path, override=False)
except ImportError:
    pass  # python-dotenv не установлен — используем os.environ как есть

# ---------------------------------------------------------------------------
# Пути
# ---------------------------------------------------------------------------
# Директория Code/
CODE_DIR: Path = Path(__file__).parent

# Корень проекта (All_csv_with_emails/)
PROJECT_DIR: Path = CODE_DIR.parent

# Выходные файлы
OUTPUT_DIR: Path = PROJECT_DIR / "data"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TXT_OUTPUT: Path = OUTPUT_DIR / "emails_unique.txt"
CSV_OUTPUT: Path = OUTPUT_DIR / "contacts.csv"
DB_OUTPUT: Path = OUTPUT_DIR / "contacts.db"
CHECKPOINT_FILE: Path = OUTPUT_DIR / "checkpoint.json"

# Директория для локального сканирования
LOCAL_SCAN_DIR: Path = OUTPUT_DIR / "Additional_files-for-check"

# ---------------------------------------------------------------------------
# Сеть
# ---------------------------------------------------------------------------
FRONTEND_URL: str = os.environ.get("FRONTEND_URL", "http://localhost:5173")
REQUEST_TIMEOUT: int = int(os.environ.get("REQUEST_TIMEOUT", "10"))
MAX_MB: int = int(os.environ.get("MAX_MB", "50"))         # Макс. размер файла для потокового парсинга
MAX_CONCURRENT: int = int(os.environ.get("MAX_CONCURRENT", "120"))

# ---------------------------------------------------------------------------
# MX Записи
# ---------------------------------------------------------------------------
MX_CACHE_LIMIT: int = int(os.environ.get("MX_CACHE_LIMIT", "10000"))

# ---------------------------------------------------------------------------
# Кэш обработанных URL (LRU — ограниченный размер в RAM)
# ---------------------------------------------------------------------------
PROCESSED_URL_CACHE_LIMIT: int = int(os.environ.get("PROCESSED_URL_CACHE_LIMIT", "20000"))

# ---------------------------------------------------------------------------
# Мониторинг памяти (MB) — при превышении воркеры замедляются + GC
# ---------------------------------------------------------------------------
MEMORY_LIMIT_MB: int = int(os.environ.get("MEMORY_LIMIT_MB", "400"))

# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------
GITHUB_TOKEN: str = os.environ.get("GITHUB_TOKEN", "")

# ---------------------------------------------------------------------------
# Google Dorks
# ---------------------------------------------------------------------------
DORK_RESULTS_PER_QUERY: int = int(os.environ.get("DORK_RESULTS_PER_QUERY", "10"))
DORK_SLEEP: float = float(os.environ.get("DORK_SLEEP", "5"))

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Мульти-бэкенд: какие фазы запускать на этом инстансе
# Допустимые: pipermail, hyperkitty, comb, github, dorks, shodan, pastebin, telegram
# "all" = запускать всё (для локальной разработки / одиночного бэкенда)
# ---------------------------------------------------------------------------
PARSER_SOURCES: set[str] = set(
    s.strip().lower()
    for s in os.environ.get("PARSER_SOURCES", "all").split(",")
    if s.strip()
)

# ---------------------------------------------------------------------------
# Распределение работы между бэкендами
# BACKEND_INDEX = номер этого бэкенда (0, 1, 2, 3, 4)
# BACKEND_TOTAL = сколько всего бэкендов
# Если не заданы — бэкенд один, берёт ВСЁ.
# ---------------------------------------------------------------------------
BACKEND_INDEX: int | None = (
    int(os.environ["BACKEND_INDEX"]) if "BACKEND_INDEX" in os.environ else None
)
BACKEND_TOTAL: int = int(os.environ.get("BACKEND_TOTAL", "1"))


def _my_slice(items: list, idx: int | None, total: int) -> list:
    """Вернуть кусок списка, принадлежащий этому бэкенду."""
    if idx is None or total <= 1:
        return items  # один бэкенд — берём всё
    return items[idx::total]

# ---------------------------------------------------------------------------
# Логирование
# ---------------------------------------------------------------------------
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")
LOG_FORMAT: str = "%(asctime)s  %(message)s"
LOG_DATE_FORMAT: str = "%H:%M:%S"

# ---------------------------------------------------------------------------
# Pipermail — список серверов (только живые, динамические)
# Распределяются между бэкендами через BACKEND_INDEX
# ---------------------------------------------------------------------------
_ALL_PIPERMAIL_SERVERS: list[str] = [
    "https://mail.python.org/pipermail/",
    "https://lists.ubuntu.com/archives/",
    "https://mail.gnome.org/archives/",
    "https://lists.freebsd.org/pipermail/",
]
PIPERMAIL_SERVERS: list[str] = _my_slice(_ALL_PIPERMAIL_SERVERS, BACKEND_INDEX, BACKEND_TOTAL)

# ---------------------------------------------------------------------------
# HyperKitty (Mailman 3) — Fedora
# URL формат: base + /archives/list/<listname>/
# ---------------------------------------------------------------------------
_ALL_HK_LISTS: list[str] = [
    "devel@lists.fedoraproject.org",
    "users@lists.fedoraproject.org",
    "test@lists.fedoraproject.org",
    "infrastructure@lists.fedoraproject.org",
    "devel-announce@lists.fedoraproject.org",
    "epel-devel@lists.fedoraproject.org",
    "kernel@lists.fedoraproject.org",
    "desktop@lists.fedoraproject.org",
    "python-devel@lists.fedoraproject.org",
    "server@lists.fedoraproject.org",
]
HYPERKITTY_SERVERS: list[dict] = [
    {
        "base": "https://lists.fedoraproject.org",
        "lists": _my_slice(_ALL_HK_LISTS, BACKEND_INDEX, BACKEND_TOTAL),
    },
]

# ---------------------------------------------------------------------------
# COMB API (ProxyNova)
# ---------------------------------------------------------------------------
COMB_API_URL: str = os.environ.get("COMB_API_URL", "https://api.proxynova.com/comb")
COMB_SLEEP: float = float(os.environ.get("COMB_SLEEP", "3.0"))
_ALL_COMB_DOMAINS: list[str] = [
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
    "aol.com", "icloud.com", "me.com", "mac.com", "protonmail.com", "pm.me",
    "gmx.com", "gmx.de", "web.de", "t-online.de", "orange.fr", "laposte.net",
    "free.fr", "sfr.fr", "libero.it", "virgilio.it", "wp.pl", "onet.pl", "o2.pl",
    "seznam.cz", "abv.bg", "mail.ru", "bk.ru", "inbox.ru", "list.ru", "yandex.ru",
    "ya.ru", "rambler.ru", "ukr.net", "i.ua", "meta.ua", "comcast.net",
    "verizon.net", "att.net", "sbcglobal.net", "cox.net", "charter.net",
    "zoho.com", "fastmail.com", "tutanota.com", "mail.com",
]
COMB_DOMAINS: list[str] = _my_slice(_ALL_COMB_DOMAINS, BACKEND_INDEX, BACKEND_TOTAL)

# ---------------------------------------------------------------------------
# НОВЫЕ ДИНАМИЧЕСКИЕ ИСТОЧНИКИ (APIs & OSINT)
# Уникальные конфигурации для добавления новых сканеров
# ---------------------------------------------------------------------------
# Shodan (Открытые порты, БД)
SHODAN_API_KEY: str = os.environ.get("SHODAN_API_KEY", "")
SHODAN_QUERIES: list[str] = [
    'http.title:"Index of /" "email" port:8080',
    'port:9200 "cluster_name"',
    'product:"MongoDB" "listDatabases"'
]

# GrayHatWarfare (Открытые Amazon S3 Buckets)
GRAYHAT_API_KEY: str = os.environ.get("GRAYHAT_API_KEY", "")

# Pastebin API (Моментальные сливы)
PASTEBIN_SCRAPE_URL: str = os.environ.get("PASTEBIN_SCRAPE_URL", "https://scrape.pastebin.com/api_scraping.php")

# Агрегаторы свежих утечек
DEHASHED_API_KEY: str = os.environ.get("DEHASHED_API_KEY", "")
DEHASHED_USERNAME: str = os.environ.get("DEHASHED_USERNAME", "")
SNUSBASE_API_KEY: str = os.environ.get("SNUSBASE_API_KEY", "")
HIBP_API_KEY: str = os.environ.get("HIBP_API_KEY", "")

# ---------------------------------------------------------------------------
# Дорки (Убраны дубликаты, добавлены новые динамические фильтры)
# ---------------------------------------------------------------------------
_ALL_EMAIL_DORKS: list[str] = [
    # Индексация серверов (строго новые/динамические)
    'intitle:"index of" "subscribers.csv"',
    'intitle:"index of" "emails.csv"',
    'intitle:"index of" "contacts.csv"',
    'intitle:"index of" "users.json"',
    'intitle:"index of" "mailing.list" "pipermail" "by date"',
    
    # Файлы БД (SQL, JSON)
    'filetype:sql "INSERT INTO" "users" "email"',
    'filetype:json "email" "password"',
    
    # Текстовые дампы и документы
    'filetype:csv "email" "name" intitle:"index of"',
    'intext:"@gmail.com" "list" filetype:txt',
    
    # Paste-сервисы
    'site:pastebin.com intext:"@gmail.com" OR intext:"@yahoo.com"',
    'site:pastebin.com "email" "password" filetype:txt',
    'site:ghostbin.co intext:"@gmail.com" OR "email" "password"',
    'site:rentry.co "combo" "@"',
    
    # GitHub (Поиск коммитов и открытых CSV с почтами)
    'site:github.com "email" "csv" pushed:>2024-01-01',
    'site:github.com "contacts" "email" filetype:csv',
    
    # Google Docs/Sheets
    'site:docs.google.com "subscribers" "email" "@"',
]
EMAIL_DORKS: list[str] = _my_slice(_ALL_EMAIL_DORKS, BACKEND_INDEX, BACKEND_TOTAL)

