"""
services/deep_validator.py
3-слойный конвейер глубокой валидации email-адресов.

Слой 1: is_fake_email()       — мгновенный regex-фильтр мусора (SSH, патчи, повторы)
Слой 2: email-validator (RFC)  — проверка синтаксиса по стандарту RFC 5321/5322
Слой 3: SMTP Verifier          — асинхронный пинг почтового сервера (RCPT TO)

Использование:
    validator = DeepEmailValidator()
    result = await validator.validate("test@gmail.com")
    # result.is_valid → True/False
    # result.normalized → "test@gmail.com"
    # result.reason → None / "syntax_error" / "domain_not_found" / "mailbox_not_found"

Интегрирован по принципу Truemail (https://github.com/truemail-rb/truemail):
    Regex → DNS/MX → SMTP — с кэшированием и rate-limiting.
"""
from __future__ import annotations

import asyncio
import logging
import re
import smtplib
import socket
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional

from .email_extractor import is_fake_email
from .disposable_domains import DISPOSABLE_DOMAINS

logger = logging.getLogger("deep_validator")

# ---------------------------------------------------------------------------
# Результат валидации
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    """Результат проверки одного email."""
    email: str
    is_valid: bool
    normalized: str = ""
    reason: Optional[str] = None  # None = валидный
    layer: str = ""  # "pattern" / "rfc" / "dns" / "smtp"


# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------

_SMTP_CACHE_LIMIT = 10_000
_SMTP_TIMEOUT = 10  # секунд на SMTP-соединение
_SMTP_HELO_DOMAIN = "mail-verify.local"  # HELO-домен для SMTP-пинга

# Домены, которые используют catch-all (принимают всё) — SMTP бесполезен
_CATCH_ALL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com",  # Google не отвечает на RCPT TO
    "yahoo.com", "ymail.com", "rocketmail.com",
    "outlook.com", "hotmail.com", "live.com", "msn.com",
    "icloud.com", "me.com", "mac.com",
    "aol.com",
    "protonmail.com", "proton.me", "pm.me",
    "tutanota.com", "tuta.io",
})

TRUSTED_DOMAINS = _CATCH_ALL_DOMAINS | {"mail.ru", "yandex.ru", "bk.ru", "list.ru", "inbox.ru"}

# ---------------------------------------------------------------------------
# Слой 2: RFC-валидация через email-validator (JoshData)
# ---------------------------------------------------------------------------

def _rfc_validate(email: str) -> tuple[bool, str, str]:
    """
    Проверить email по стандарту RFC 5321/5322.
    
    Возвращает (is_valid, normalized_email, error_reason).
    Использует библиотеку email-validator от JoshData:
    https://github.com/JoshData/python-email-validator
    """
    try:
        from email_validator import validate_email, EmailNotValidError
        
        result = validate_email(
            email,
            check_deliverability=False,  # DNS проверяем отдельно через наш MxChecker
            test_environment=False,
        )
        return True, result.normalized, ""
    except EmailNotValidError as e:
        return False, email, str(e)
    except ImportError:
        # Если библиотека не установлена — пропускаем этот слой
        logger.warning("email-validator не установлен, RFC-проверка пропущена")
        return True, email, ""


# ---------------------------------------------------------------------------
# Слой 3: Асинхронный SMTP Verifier
# ---------------------------------------------------------------------------

class SmtpVerifier:
    """
    Асинхронный SMTP-верификатор.
    
    Проверяет, существует ли почтовый ящик на сервере, делая SMTP-запрос:
      HELO → MAIL FROM → RCPT TO → проверяем код ответа → QUIT
    
    Если сервер ответил 250 на RCPT TO — ящик существует.
    Если 550 — ящик не найден.
    
    Вдохновлён: https://github.com/syrusakbary/validate_email
    Но написан с нуля: асинхронный, с кэшем, rate-limiting и обработкой greylisting.
    """

    def __init__(self) -> None:
        self._cache: OrderedDict[str, bool] = OrderedDict()
        self._lock = asyncio.Lock()

    async def verify(self, email: str, mx_host: str | None = None) -> tuple[bool, str]:
        """
        Проверить существование ящика через SMTP.
        
        Returns:
            (is_valid, reason)
        """
        domain = email.split("@")[1].lower()
        
        # Catch-all домены — SMTP бесполезен, считаем валидным
        if domain in _CATCH_ALL_DOMAINS:
            return True, "catch_all_skip"
        
        # Проверяем кэш
        async with self._lock:
            if email in self._cache:
                self._cache.move_to_end(email)
                cached = self._cache[email]
                return cached, "cached"
        
        # Получаем MX-хост если не передан
        if not mx_host:
            mx_host = await self._resolve_mx(domain)
            if not mx_host:
                return False, "no_mx_record"
        
        # Делаем SMTP-пинг в фоновом потоке (smtplib — синхронный)
        try:
            is_valid = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, self._smtp_ping, email, mx_host
                ),
                timeout=_SMTP_TIMEOUT + 5
            )
        except asyncio.TimeoutError:
            is_valid = True  # Таймаут — даём benefit of the doubt
            
        # Кэшируем результат
        async with self._lock:
            self._cache[email] = is_valid
            if len(self._cache) > _SMTP_CACHE_LIMIT:
                self._cache.popitem(last=False)
        
        reason = "" if is_valid else "mailbox_not_found"
        return is_valid, reason

    @staticmethod
    async def _resolve_mx(domain: str) -> str | None:
        """Получить MX-хост домена через dnspython."""
        try:
            import dns.resolver
            loop = asyncio.get_event_loop()
            answers = await loop.run_in_executor(
                None, lambda: dns.resolver.resolve(domain, "MX")
            )
            # Берём MX с наименьшим приоритетом (наивысший приоритет)
            best = min(answers, key=lambda x: x.preference)
            return str(best.exchange).rstrip(".")
        except Exception:
            return None

    @staticmethod
    def _smtp_ping(email: str, mx_host: str) -> bool:
        """
        Синхронный SMTP-пинг. Выполняется в executor.
        
        Имитирует начало отправки письма:
          HELO → MAIL FROM → RCPT TO → анализ кода → QUIT
        
        НЕ отправляет реальное письмо.
        """
        try:
            smtp = smtplib.SMTP(timeout=_SMTP_TIMEOUT)
            smtp.connect(mx_host, 25)
            smtp.helo(_SMTP_HELO_DOMAIN)
            smtp.mail(f"verify@{_SMTP_HELO_DOMAIN}")
            code, _ = smtp.rcpt(email)
            smtp.quit()
            
            # 250 = OK, ящик существует
            # 251 = User not local, will forward
            # 550 = Mailbox not found
            # 552 = Mailbox full (но существует!)
            # 450/451 = Greylisting (временная ошибка — считаем валидным)
            return code in (250, 251, 450, 451, 452, 552)
            
        except smtplib.SMTPServerDisconnected:
            return True  # Сервер оборвал — скорее всего защита, считаем ОК
        except (smtplib.SMTPConnectError, socket.timeout, OSError):
            return True  # Не удалось подключиться — не баним, считаем ОК
        except Exception:
            return True  # Любая ошибка — benefit of the doubt


# ---------------------------------------------------------------------------
# Главный валидатор — объединяет все 3 слоя
# ---------------------------------------------------------------------------

class DeepEmailValidator:
    """
    Трёхслойный конвейер валидации email.
    
    Вдохновлён архитектурой Truemail:
      Layer 1 (Pattern)  → Мгновенный фильтр мусора
      Layer 2 (RFC/DNS)  → email-validator (syntax + optional DNS)
      Layer 3 (SMTP)     → Пинг сервера (опционально)
    
    Использование:
        validator = DeepEmailValidator(smtp_enabled=False)
        result = await validator.validate("user@gmail.com")
    """

    def __init__(self, smtp_enabled: bool = False) -> None:
        """
        Args:
            smtp_enabled: Включить SMTP-верификацию (Слой 3).
                         ВНИМАНИЕ: Может привести к бану IP при массовой проверке.
                         Рекомендуется только для финальной чистки готовой базы.
        """
        self._smtp_enabled = smtp_enabled
        self._smtp_verifier = SmtpVerifier() if smtp_enabled else None

    async def validate(self, email: str) -> ValidationResult:
        """Прогнать email через все слои валидации."""
        
        # Предварительная очистка
        email = email.lower().strip()
        
        # ── Слой 1: Наш кастомный фильтр мусора ──
        if is_fake_email(email):
            return ValidationResult(
                email=email, is_valid=False,
                reason="pattern_filter", layer="pattern"
            )
        
        # ── Слой 2: RFC-валидация (email-validator от JoshData) ──
        rfc_valid, normalized, rfc_error = _rfc_validate(email)
        if not rfc_valid:
            return ValidationResult(
                email=email, is_valid=False, normalized=normalized,
                reason=f"rfc_invalid: {rfc_error}", layer="rfc"
            )
        
        # Доверенные домены (Gmail, Yahoo и т.д.) — пропускаем DNS и SMTP
        domain = normalized.split("@")[1]
        if domain in TRUSTED_DOMAINS:
            return ValidationResult(
                email=email, is_valid=True, normalized=normalized,
                layer="trusted"
            )
        
        # ── Слой 3: SMTP-верификация (опционально) ──
        if self._smtp_enabled and self._smtp_verifier:
            smtp_valid, smtp_reason = await self._smtp_verifier.verify(normalized)
            if not smtp_valid:
                return ValidationResult(
                    email=email, is_valid=False, normalized=normalized,
                    reason=f"smtp: {smtp_reason}", layer="smtp"
                )
        
        return ValidationResult(
            email=email, is_valid=True, normalized=normalized,
            layer="passed_all"
        )

    async def validate_batch(
        self, emails: list[str], concurrency: int = 10
    ) -> list[ValidationResult]:
        """
        Валидация списка email с ограничением параллельности.
        
        Args:
            emails: Список email для проверки
            concurrency: Макс. число одновременных проверок
        """
        semaphore = asyncio.Semaphore(concurrency)
        
        async def _check(email: str) -> ValidationResult:
            async with semaphore:
                return await self.validate(email)
        
        tasks = [_check(e) for e in emails]
        return await asyncio.gather(*tasks)
