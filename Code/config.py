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
MAX_CONCURRENT: int = int(os.environ.get("MAX_CONCURRENT", "60"))

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
# Мульти-бэкенд: какие фазы запускать на этом инстансе
# Допустимые: pipermail, hyperkitty, comb, github, dorks
# "all" = запускать всё (для локальной разработки / одиночного бэкенда)
# ---------------------------------------------------------------------------
PARSER_SOURCES: set[str] = set(
    s.strip().lower()
    for s in os.environ.get("PARSER_SOURCES", "all").split(",")
    if s.strip()
)

# ---------------------------------------------------------------------------
# Логирование
# ---------------------------------------------------------------------------
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")
LOG_FORMAT: str = "%(asctime)s  %(message)s"
LOG_DATE_FORMAT: str = "%H:%M:%S"

# ---------------------------------------------------------------------------
# Pipermail — список серверов (только живые, динамические)
# ---------------------------------------------------------------------------
PIPERMAIL_SERVERS: list[str] = [
    "https://lists.ubuntu.com/archives/",
]

# ---------------------------------------------------------------------------
# HyperKitty (Mailman 3) — Fedora
# URL формат: base + /archives/list/<listname>/
# ---------------------------------------------------------------------------
HYPERKITTY_SERVERS: list[dict] = [
    {
        "base": "https://lists.fedoraproject.org",
        "lists": [
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
        ],
    },
]

# ---------------------------------------------------------------------------
# COMB API (бесплатный, без ключа — ProxyNova)
# ---------------------------------------------------------------------------
COMB_API_URL: str = os.environ.get("COMB_API_URL", "https://api.proxynova.com/comb")
COMB_SLEEP: float = float(os.environ.get("COMB_SLEEP", "3.0"))
COMB_DOMAINS: list[str] = [
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "mail.ru", "yandex.ru", "protonmail.com", "aol.com",
    "icloud.com", "zoho.com", "gmx.com", "fastmail.com",
    "tutanota.com", "mail.com", "inbox.ru", "list.ru",
    "bk.ru", "rambler.ru", "live.com", "msn.com",
]

# ---------------------------------------------------------------------------
# Дорки
# ---------------------------------------------------------------------------
EMAIL_DORKS: list[str] = [
    'intitle:"index of" "subscribers.csv"',
    'intitle:"index of" "emails.csv"',
    'filetype:csv intext:"@" -intext:"example.com"',
    'filetype:txt intext:"@" -intext:"example.com"',
    'inurl:"wp-content/uploads/mc4wp-debug.log"',
    'intext:"@gmail.com" filetype:csv OR filetype:txt',
    # Paste-сайты (динамические источники)
    'site:pastebin.com intext:"@gmail.com"',
    'site:pastebin.com intext:"@yahoo.com" intext:"@"',
    'site:pastebin.com "email" "password" filetype:txt',
    'site:dpaste.org intext:"@" -intext:"example"',
]
