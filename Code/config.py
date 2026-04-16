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
MAX_MB: int = int(os.environ.get("MAX_MB", "5"))         # Макс. размер одного HTTP-ответа (МБ)
MAX_CONCURRENT: int = int(os.environ.get("MAX_CONCURRENT", "20"))  # Параллельных воркеров (было 100)

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
# Логирование
# ---------------------------------------------------------------------------
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")
LOG_FORMAT: str = "%(asctime)s  %(message)s"
LOG_DATE_FORMAT: str = "%H:%M:%S"

# ---------------------------------------------------------------------------
# Pipermail — список серверов
# ---------------------------------------------------------------------------
PIPERMAIL_SERVERS: list[str] = [
    "https://mail.python.org/pipermail/",
    "https://lists.ubuntu.com/archives/",
    "https://mail.gnome.org/archives/",
    "https://lists.freebsd.org/pipermail/",
    "https://lists.fedoraproject.org/archives/",
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
]
