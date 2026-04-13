"""
infrastructure/csv_writer.py
Репозиторий контактов на основе CSV + TXT файлов.

Реализует IContactRepository.
  - load() — читает contacts.csv → dict[email, Contact]
  - save() — перезаписывает contacts.csv и emails_unique.txt
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path

from ..core.entities import Contact
from ..core.interfaces import IContactRepository
from ..services.email_extractor import is_fake_email

log = logging.getLogger(__name__)


class CsvContactRepository(IContactRepository):
    """
    Персистентное хранилище контактов в формате CSV + TXT.

    Args:
        csv_path: Путь к файлу contacts.csv
        txt_path: Путь к файлу emails_unique.txt
    """

    CSV_HEADER = ["Имя", "Фамилия", "Email"]

    def __init__(self, csv_path: Path, txt_path: Path) -> None:
        self._csv = csv_path
        self._txt = txt_path

    # ------------------------------------------------------------------
    # IContactRepository implementation
    # ------------------------------------------------------------------

    def load(self) -> dict[str, Contact]:
        """
        Загрузить контакты из CSV.

        Returns:
            Словарь ``{email: Contact}``.
        """
        contacts: dict[str, Contact] = {}
        if not self._csv.exists():
            return contacts

        try:
            with open(self._csv, encoding="utf-8") as f:
                reader = csv.reader(f)
                next(reader, None)  # пропустить заголовок
                for row in reader:
                    if len(row) < 3:
                        continue
                    first, last, email = row[0], row[1], row[2].lower().strip()
                    if email and "@" in email and not is_fake_email(email):
                        contacts[email] = Contact(
                            email=email,
                            first_name=first,
                            last_name=last,
                        )
            log.info("📖 Загружено %d контактов из %s", len(contacts), self._csv.name)
        except Exception as exc:
            log.error("✗ Ошибка чтения %s: %s", self._csv, exc)

        return contacts

    def save(self, contacts: dict[str, Contact]) -> None:
        """
        Перезаписать CSV и TXT файлы.

        Args:
            contacts: Словарь ``{email: Contact}``.
        """
        # Сортируем по email для детерминированного вывода
        sorted_contacts = sorted(contacts.values(), key=lambda c: c.email)

        # --- TXT (только адреса) ---
        try:
            with open(self._txt, "w", encoding="utf-8") as f:
                f.write("\n".join(c.email for c in sorted_contacts) + "\n")
        except Exception as exc:
            log.error("✗ Ошибка записи %s: %s", self._txt, exc)

        # --- CSV ---
        try:
            with open(self._csv, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(self.CSV_HEADER)
                for contact in sorted_contacts:
                    writer.writerow([contact.first_name, contact.last_name, contact.email])
            log.info("💾 Сохранено %d контактов → %s", len(sorted_contacts), self._csv.name)
        except Exception as exc:
            log.error("✗ Ошибка записи %s: %s", self._csv, exc)

    # ------------------------------------------------------------------
    # Additional helpers
    # ------------------------------------------------------------------

    def merge_into(
        self,
        existing: dict[str, Contact],
        new_contacts: list[Contact],
    ) -> int:
        """
        Добавить новые контакты в *existing*, не перезаписывая существующие.

        Returns:
            Количество добавленных контактов.
        """
        added = 0
        for contact in new_contacts:
            if contact.email and contact.email not in existing:
                existing[contact.email] = contact
                added += 1
        return added
