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
# COMB API (бесплатный, без ключа — ProxyNova)
# ---------------------------------------------------------------------------
COMB_API_URL: str = os.environ.get("COMB_API_URL", "https://api.proxynova.com/comb")
COMB_SLEEP: float = float(os.environ.get("COMB_SLEEP", "3.0"))
_ALL_COMB_DOMAINS: list[str] = [
    # === Глобальные гиганты (95% мирового трафика) ===
    "gmail.com", "googlemail.com",
    "yahoo.com", "ymail.com", "rocketmail.com",
    "outlook.com", "hotmail.com", "live.com", "msn.com",
    "aol.com",
    "icloud.com", "me.com", "mac.com",
    "protonmail.com", "proton.me", "pm.me",
    # === Европа (крупные провайдеры с живыми пользователями) ===
    "gmx.com", "gmx.de", "gmx.net", "gmx.at",
    "web.de",
    "t-online.de",
    "orange.fr", "laposte.net", "free.fr", "sfr.fr",
    "libero.it", "virgilio.it",
    "wp.pl", "onet.pl", "interia.pl", "o2.pl",
    "seznam.cz",
    "abv.bg",
    # === СНГ / Россия ===
    "mail.ru", "bk.ru", "inbox.ru", "list.ru",
    "yandex.ru", "yandex.com", "ya.ru",
    "rambler.ru",
    "ukr.net", "i.ua", "meta.ua",
    # === США / Канада (ISP-провайдеры) ===
    "comcast.net", "verizon.net", "att.net",
    "sbcglobal.net", "cox.net", "charter.net",
    # === Прочие крупные ===
    "zoho.com",
    "fastmail.com",
    "tutanota.com", "tuta.io",
    "mail.com",
]
COMB_DOMAINS: list[str] = _my_slice(_ALL_COMB_DOMAINS, BACKEND_INDEX, BACKEND_TOTAL)

# ---------------------------------------------------------------------------
# Дорки
# ---------------------------------------------------------------------------
_ALL_EMAIL_DORKS: list[str] = [
    # === Файлы с email-адресами на открытых серверах ===
    'intitle:"index of" "subscribers.csv"',
    'intitle:"index of" "emails.csv"',
    'intitle:"index of" "contacts.csv"',
    'intitle:"index of" "members.csv"',
    'intitle:"index of" "users.csv"',
    'intitle:"index of" "mailing" filetype:csv',
    'intitle:"index of" "newsletter" filetype:txt',
    # === CSV / TXT с email-адресами ===
    'filetype:csv intext:"@" -intext:"example.com"',
    'filetype:txt intext:"@" -intext:"example.com"',
    'filetype:csv "email" "first" "last" -intext:"example"',
    'filetype:xlsx intext:"@gmail.com" intext:"@yahoo.com"',
    # === Логи и утечки ===
    'inurl:"wp-content/uploads/mc4wp-debug.log"',
    'intext:"@gmail.com" filetype:csv OR filetype:txt',
    'intext:"@yahoo.com" filetype:csv OR filetype:txt',
    'intext:"@outlook.com" filetype:csv OR filetype:txt',
    'intext:"@hotmail.com" filetype:csv OR filetype:txt',
    # === Google Docs / Sheets (публичные) ===
    'site:docs.google.com "email" "@gmail.com"',
    'site:docs.google.com "subscribers" "email" "@"',
    # === Paste-сайты (динамические источники) ===
    'site:pastebin.com intext:"@gmail.com"',
    'site:pastebin.com intext:"@yahoo.com" intext:"@"',
    'site:pastebin.com "email" "password" filetype:txt',
    'site:dpaste.org intext:"@" -intext:"example"',
    'site:ghostbin.co intext:"@gmail.com"',
    'site:ghostbin.co "email" "password"',
    'site:rentry.co intext:"@yahoo.com"',
    'site:rentry.co "combo" "@"',
    # === Конференции, университеты, организации ===
    'intext:"attendee" intext:"@" filetype:pdf',
    'intext:"participant" "email" filetype:csv',
    'intext:"directory" "email" "phone" filetype:csv',
    'intext:"roster" "email" filetype:xlsx OR filetype:csv',
    # === GitHub (открытые репозитории с данными) ===
    'site:github.com "email" "csv" "@gmail.com"',
    'site:github.com "contacts" "email" filetype:csv',
]
EMAIL_DORKS: list[str] = _my_slice(_ALL_EMAIL_DORKS, BACKEND_INDEX, BACKEND_TOTAL)
