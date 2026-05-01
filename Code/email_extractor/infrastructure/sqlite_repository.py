import sqlite3
import threading
from typing import Generator
from collections import OrderedDict
from pathlib import Path
import logging

from ..core.entities import Contact
from config import PROCESSED_URL_CACHE_LIMIT

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
        
        # LRU-кэш вместо безлимитного set: максимум PROCESSED_URL_CACHE_LIMIT записей
        self._processed_urls_cache: OrderedDict[str, None] = OrderedDict()
        self._cache_limit = PROCESSED_URL_CACHE_LIMIT
        self._load_processed_cache()

    def _load_processed_cache(self) -> None:
        """Предзагрузка ПОСЛЕДНИХ N обработанных URL (LRU, не все подряд)."""
        with self._lock:
            # Загружаем только последние _cache_limit URL для экономии RAM
            cursor = self._conn.execute(
                "SELECT url FROM processed_urls ORDER BY rowid DESC LIMIT ?",
                (self._cache_limit,)
            )
            for row in cursor:
                self._processed_urls_cache[row[0]] = None

    def _init_db(self) -> None:
        with self._lock:
            # WAL-режим: быстрее для частых INSERT и параллельных чтений
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")

            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS contacts (
                    email TEXT PRIMARY KEY,
                    first_name TEXT,
                    last_name TEXT
                ) WITHOUT ROWID
            """)
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_email ON contacts(email)"
            )

            # Таблица для хранения обработанных URL (вместо RAM-списка checkpoint)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_urls (
                    url TEXT PRIMARY KEY
                ) WITHOUT ROWID
            """)

    def _cache_add(self, url: str) -> None:
        """Добавить URL в LRU-кэш с вытеснением старых записей."""
        if url in self._processed_urls_cache:
            self._processed_urls_cache.move_to_end(url)
            return
        self._processed_urls_cache[url] = None
        # Вытеснить самый старый если превышен лимит
        while len(self._processed_urls_cache) > self._cache_limit:
            self._processed_urls_cache.popitem(last=False)

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

    def add_contacts_bulk(self, contacts: list[Contact]) -> int:
        """Пакетное добавление контактов. Возвращает количество успешно добавленных (уникальных)."""
        if not contacts:
            return 0
        added = 0
        with self._lock:
            self._conn.execute("BEGIN TRANSACTION")
            try:
                for c in contacts:
                    try:
                        self._conn.execute(
                            "INSERT INTO contacts (email, first_name, last_name) VALUES (?, ?, ?)",
                            (c.email, c.first_name, c.last_name),
                        )
                        added += 1
                    except sqlite3.IntegrityError:
                        pass
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return added

    def clear_all(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM contacts")

    def truncate_wal(self) -> None:
        """Принудительно сбрасывает WAL-файл в основную БД и очищает его.
        Это предотвращает разрастание .db-wal файла и утечку Linux Page Cache,
        из-за которой Railway показывает высокое потребление ОЗУ."""
        with self._lock:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception as e:
                log.debug(f"Failed to truncate WAL: {e}")

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

    def get_all_emails(self) -> list[str]:
        """Возвращает все email из базы (для кросс-бэкенд дедупликации)."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT email FROM contacts ORDER BY email")
            return [row[0] for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Processed URLs checkpoint (LRU-кэш + SQLite fallback)
    # ------------------------------------------------------------------

    def is_url_processed(self, url: str) -> bool:
        """Проверить, был ли URL уже обработан. Сначала LRU-кэш O(1), потом SQLite fallback."""
        # Быстрая проверка в RAM (O(1))
        if url in self._processed_urls_cache:
            self._processed_urls_cache.move_to_end(url)  # обновить позицию LRU
            return True
        # Fallback: проверка в SQLite (медленно, но редко — только при cache-miss)
        with self._lock:
            cursor = self._conn.execute(
                "SELECT 1 FROM processed_urls WHERE url = ? LIMIT 1", (url,)
            )
            found = cursor.fetchone() is not None
        if found:
            # Добавить обратно в кэш для будущих быстрых проверок
            self._cache_add(url)
        return found

    def mark_url_processed(self, url: str) -> None:
        """Пометить URL как обработанный. (Синхронно: в кэш + в БД)"""
        if url not in self._processed_urls_cache:
            self._cache_add(url)
            with self._lock:
                self._conn.execute(
                    "INSERT OR IGNORE INTO processed_urls (url) VALUES (?)", (url,)
                )

    def mark_urls_processed_bulk(self, urls: list[str]) -> None:
        """Пакетное добавление URL в кэш и в БД."""
        new_urls = [u for u in urls if u not in self._processed_urls_cache]
        if not new_urls:
            return
        for u in new_urls:
            self._cache_add(u)
        with self._lock:
            self._conn.execute("BEGIN TRANSACTION")
            try:
                self._conn.executemany("INSERT OR IGNORE INTO processed_urls (url) VALUES (?)", [(u,) for u in new_urls])
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def get_processed_count(self) -> int:
        """Количество обработанных URL (для логов)."""
        with self._lock:
            cursor = self._conn.execute("SELECT COUNT(*) FROM processed_urls")
            return cursor.fetchone()[0]

    def has_names(self) -> bool:
        """Проверить, есть ли хотя бы одна запись с именем или фамилией (не NULL)."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT 1 FROM contacts WHERE first_name IS NOT NULL OR last_name IS NOT NULL LIMIT 1"
            )
            return cursor.fetchone() is not None

    def clear_processed_urls(self) -> None:
        """Очистить таблицу обработанных URL (при отмене / новом запуске)."""
        self._processed_urls_cache.clear()
        with self._lock:
            self._conn.execute("DELETE FROM processed_urls")
