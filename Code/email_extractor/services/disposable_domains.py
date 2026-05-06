"""
services/disposable_domains.py
Блоклист доменов одноразовых (disposable) и временных почтовых сервисов.

Письма, отправленные на эти домены, ГАРАНТИРОВАННО портят репутацию:
- Ящики живут 10 минут → bounce rate 100%
- Многие являются спам-ловушками антиспам-организаций
- Ни один реальный человек не использует их как основную почту

Обновлять этот список по мере обнаружения новых сервисов.
"""
from __future__ import annotations
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Одноразовые / временные почтовые домены (disposable email services)
# ---------------------------------------------------------------------------

_FALLBACK_DOMAINS = {
    # === Самые популярные (топ-50 по трафику) ===
    "tempmail.com", "temp-mail.org", "temp-mail.io",
    "guerrillamail.com", "guerrillamail.net", "guerrillamail.org",
    "guerrillamail.de", "guerrillamail.biz", "guerrillamailblock.com",
    "grr.la", "sharklasers.com", "guerrillamail.info",
    "yopmail.com", "yopmail.fr", "yopmail.net",
    "10minutemail.com", "10minutemail.net", "10minutemail.org",
    "minutemail.com", "tempail.com", "tempr.email",
    "throwaway.email", "throwawaymail.com",
    "dispostable.com", "maildrop.cc", "mailnesia.com", "mailsac.com",
    "trashmail.com", "trashmail.me", "trashmail.net", "trashmail.org",
    "trashemail.de", "trashmail.at",
    "mailinator.com", "mailinator.net", "mailinator2.com",
    "sogetthis.com", "mailinater.com", "mailinator.org",
    "spam4.me", "spamgourmet.com", "spamgourmet.net",
    "fakeinbox.com", "fakemail.fr", "fakemail.net",
    "getnada.com", "nada.email", "nada.ltd",
    "mohmal.com", "mohmal.im", "mohmal.in", "mohmal.tech",
}

def _load_disposable_domains() -> frozenset[str]:
    """
    Загружает базу из ~57,000 мусорных доменов из файла (собранную из GitHub).
    Если файла нет, использует встроенный Fallback-список.
    """
    data_dir = Path(__file__).parent.parent.parent.parent / "data"
    blocklist_path = data_dir / "disposable_blocklist.txt"
    
    domains = set(_FALLBACK_DOMAINS)
    if blocklist_path.exists():
        try:
            with open(blocklist_path, "r", encoding="utf-8") as f:
                for line in f:
                    d = line.strip().lower()
                    if d:
                        domains.add(d)
        except Exception:
            pass
            
    return frozenset(domains)

DISPOSABLE_DOMAINS: frozenset[str] = _load_disposable_domains()

# ---------------------------------------------------------------------------
# Ролевые / технические префиксы email — не личные, а корпоративные ящики
# Рассылка на них = мгновенное попадание в спам + жалоба
# ---------------------------------------------------------------------------
ROLE_PREFIXES: frozenset[str] = frozenset({
    # Административные
    "admin", "administrator", "root", "sysadmin", "postmaster",
    "hostmaster", "webmaster", "domainadmin",
    # Поддержка и сервис
    "support", "help", "helpdesk", "service", "customerservice",
    "techsupport", "tech", "feedback",
    # Безопасность и злоупотребления
    "abuse", "security", "spam", "phishing", "cert",
    "noc", "soc",
    # Общие корпоративные
    "info", "information", "contact", "contacts",
    "hello", "enquiry", "inquiry", "office", "reception",
    # Продажи и маркетинг
    "sales", "marketing", "advertising", "ads", "promo",
    "newsletter", "subscribe", "unsubscribe",
    "partnerships", "partner", "affiliate",
    # Юридические и HR
    "legal", "compliance", "hr", "hiring", "jobs", "careers",
    "recruit", "recruitment",
    # Финансы
    "billing", "accounting", "finance", "invoices", "payments",
    # Пресса и PR
    "press", "media", "pr", "communications", "comms",
    # Общие нежелательные
    "noreply", "no-reply", "no.reply", "donotreply", "do-not-reply",
    "mailer-daemon", "mailer", "daemon",
    "bounce", "bounces", "return", "returns",
    "devnull", "null", "void", "blackhole",
    "postoffice", "mail", "email", "e-mail",
    "team", "staff", "group", "all", "everyone", "everybody",
    # Боты и автоматика
    "bot", "robot", "auto", "autoresponder", "autoreply",
    "cron", "system", "notification", "notifications",
    "alert", "alerts", "monitor", "monitoring",
    "test", "testing", "debug", "dev", "staging",
    # Генерические / не-личные (из COMB-мусора)
    "user", "dummy", "master", "manager", "app", "web", "www",
    "pro", "git", "bugzilla", "server", "client", "guest",
    "demo", "example", "default", "anonymous", "unknown",
    "nobody", "operator", "owner", "sender", "receiver",
    "list", "lists", "announce", "news",
})
