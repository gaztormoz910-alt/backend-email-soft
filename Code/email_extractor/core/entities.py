"""
core/entities.py
Доменные сущности — не зависят ни от каких внешних библиотек.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Contact:
    """
    Единственная доменная единица системы.

    Attributes:
        email:      Email-адрес в нижнем регистре.
        first_name: Имя (может быть пустой строкой).
        last_name:  Фамилия (может быть пустой строкой).
    """
    __slots__ = ('email', 'first_name', 'last_name')
    email: str
    first_name: str = field(default="")
    last_name: str = field(default="")

    # ------------------------------------------------------------------
    # Дополнительные конструкторы
    # ------------------------------------------------------------------

    @classmethod
    def from_email_only(cls, email: str) -> "Contact":
        """Создать контакт только из email."""
        return cls(email=email.lower().strip())

    @classmethod
    def from_tuple(cls, first: str, last: str, email: str) -> "Contact":
        """Удобный конструктор из тройки (first, last, email)."""
        return cls(
            email=email.lower().strip(),
            first_name=first.strip(),
            last_name=last.strip(),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def is_valid(self) -> bool:
        """Минимальная санитарная проверка: есть @, длина ≥ 6, есть домен."""
        e = self.email
        if len(e) < 6 or "@" not in e:
            return False
        local, _, domain = e.partition("@")
        return bool(local) and len(domain) >= 3 and "." in domain

    @property
    def domain(self) -> str:
        """Домен email-адреса."""
        return self.email.split("@", 1)[1] if "@" in self.email else ""

    @property
    def display_name(self) -> str:
        """Полное имя или пустая строка."""
        parts = [p for p in (self.first_name, self.last_name) if p]
        return " ".join(parts)

    def __str__(self) -> str:
        name = self.display_name
        return f"{name} <{self.email}>" if name else self.email
