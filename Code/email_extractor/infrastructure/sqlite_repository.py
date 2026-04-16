import sqlite3
import threading
from typing import Generator
from pathlib import Path
import logging

from ..core.entities import Contact

log = logging.getLogger(__name__)

class SqliteContactRepository:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None  # autocommit is on
        )
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            # WAL-режим: быстрее для частых INSERT и параллельных чтений
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")

            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS contacts (
                    email TEXT KEY,
                    first_name TEXT,
                    last_name TEXT
                )
            """)
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_email ON contacts(email)"
            )

            # Таблица для хранения обработанных URL (вместо RAM-списка checkpoint)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_urls (
                    url TEXT PRIMARY KEY
                )
            """)

    # ------------------------------------------------------------------
    # Contacts
    # ------------------------------------------------------------------

    def get_count(self) -> int:
        with self._lock:
            cursor = self._conn.execute("SELECT COUNT(*) FROM contacts")
            return cursor.fetchone()[0]

    def add_if_not_exists(self, contact: Contact) -> bool:
        """
        Returns True if the contact was successfully added.
        Returns False if the email already existed.
        """
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO contacts (email, first_name, last_name) VALUES (?, ?, ?)",
                    (contact.email, contact.first_name, contact.last_name),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def clear_all(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM contacts")

    def stream_csv(self) -> Generator[str, None, None]:
        yield "Имя,Фамилия,Email\n"
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT first_name, last_name, email FROM contacts ORDER BY email")
            while True:
                rows = cursor.fetchmany(1000)
                if not rows:
                    break
                for row in rows:
                    fname = row[0] if row[0] else ""
                    lname = row[1] if row[1] else ""
                    email = row[2]
                    yield f"{fname},{lname},{email}\n"

    def stream_txt(self) -> Generator[str, None, None]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT email FROM contacts ORDER BY email")
            while True:
                rows = cursor.fetchmany(1000)
                if not rows:
                    break
                for row in rows:
                    yield f"{row[0]}\n"

    # ------------------------------------------------------------------
    # Processed URLs checkpoint (хранится на диске, а не в RAM)
    # ------------------------------------------------------------------

    def is_url_processed(self, url: str) -> bool:
        """Проверить, был ли URL уже обработан. O(log n) по индексу SQLite."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT 1 FROM processed_urls WHERE url = ? LIMIT 1", (url,)
            )
            return cursor.fetchone() is not None

    def mark_url_processed(self, url: str) -> None:
        """Пометить URL как обработанный. Игнорирует дубликаты."""
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO processed_urls (url) VALUES (?)", (url,)
            )

    def get_processed_count(self) -> int:
        """Количество обработанных URL (для логов)."""
        with self._lock:
            cursor = self._conn.execute("SELECT COUNT(*) FROM processed_urls")
            return cursor.fetchone()[0]

    def clear_processed_urls(self) -> None:
        """Очистить таблицу обработанных URL (при отмене / новом запуске)."""
        with self._lock:
            self._conn.execute("DELETE FROM processed_urls")
