#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EMAIL EXTRACTOR v12.0 FINAL — MAXIMUM OVERDRIVE
- Асинхронный сбор из Pipermail, Google Dorks, GitHub
- Локальное сканирование файлов любых форматов
- Проверка MX с кэшем (только живые адреса)
- Прогресс-бары и отображение времени работы
"""
import sys, asyncio, re, csv, io, os, time, json, gzip, random, logging, urllib.parse
from datetime import datetime
from pathlib import Path
import httpx
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
import dns.resolver
from googlesearch import search as google_search

# Подавление лишних логов
import logging as lg
lg.getLogger('fake_useragent').setLevel(lg.ERROR)
lg.getLogger('httpx').setLevel(lg.WARNING)
lg.getLogger('dns').setLevel(lg.WARNING)

# Пытаемся импортировать tqdm для прогресс-баров
try:
    from tqdm.asyncio import tqdm as async_tqdm
    from tqdm import tqdm as sync_tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False
    # Заглушка, чтобы код не падал
    class fake_tqdm:
        def __init__(self, iterable=None, desc=None, total=None, **kwargs):
            self.iterable = iterable
            self.desc = desc
            self.total = total
            self.n = 0
        def update(self, n=1):
            self.n += n
            if self.total:
                print(f"\r{self.desc}: {self.n}/{self.total}", end="", flush=True)
        def __enter__(self): return self
        def __exit__(self, *args): print()
    async_tqdm = fake_tqdm
    sync_tqdm = fake_tqdm

# ---------- КОНФИГУРАЦИЯ ----------
SCRIPT_DIR = Path(__file__).parent
OUTPUT_DIR = SCRIPT_DIR.parent
TXT_OUTPUT = OUTPUT_DIR / "emails_unique.txt"
CSV_OUTPUT = OUTPUT_DIR / "contacts.csv"
CHECKPOINT_FILE = OUTPUT_DIR / "checkpoint.json"
LOCAL_SCAN_DIR = OUTPUT_DIR / "Additional_files-for-check"

REQUEST_TIMEOUT = 10
MAX_MB = 20
MAX_CONCURRENT = 100
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
DORK_RESULTS_PER_QUERY = 10
DORK_SLEEP = 5

ua = UserAgent(browsers=['chrome', 'firefox', 'edge'], os=['windows', 'macos', 'linux'], fallback='chrome')

EMAIL_RE = re.compile(r'\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b')
FAKE_PATTERNS = [re.compile(p, re.I) for p in [
    r'^random_\d+@', r'^email\d+@email\.com$', r'^first\d+@', r'^test\d*@', r'^user\d*@',
    r'^sample\d*@', r'^noreply@', r'^no-reply@', r'^donotreply@', r'^postmaster@',
    r'^mailer-daemon@', r'^bounce@', r'^admin@(example|test|domain|localhost)'
]]
IGNORE = {"email@example.com","test@test.com","user@example.com","no-reply@example.com",
          "admin@example.com","example@example.com","noreply@example.com","info@example.com",
          "mail@example.com","support@example.com","contact@example.com","user@test.com",
          "email@domain.com","yourname@domain.com","name@example.com","email@email.com"}

def is_fake_email(email: str) -> bool:
    e = email.lower().strip()
    if e in IGNORE or len(e) < 6 or "@" not in e: return True
    local, domain = e.rsplit("@", 1)
    if len(domain) < 3: return True
    if domain in {"example.com","test.com","domain.com","localhost.com","email.com","mail.com","placeholder.com"}:
        return True
    for pat in FAKE_PATTERNS:
        if pat.search(local + "@"): return True
    return False

def split_name(full: str):
    full = re.sub(r'["""\'\(\)\[\]]', '', full).strip()
    if not full or len(full) > 60 or any(c in full for c in '@<>{}'): return ("","")
    p = full.split()
    return (p[0].capitalize(), p[-1].capitalize() if len(p) > 1 else "")

def format_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0: return f"{h}ч {m}м {s}с"
    elif m > 0: return f"{m}м {s}с"
    else: return f"{s}с"

# ---------- ЛОКАЛЬНОЕ СКАНИРОВАНИЕ ----------
def find_files_recursive(directory: Path, extensions: set) -> list:
    found = []
    try:
        for entry in directory.iterdir():
            if entry.is_file() and (extensions == {"*"} or entry.suffix.lower() in extensions):
                found.append(entry)
            elif entry.is_dir():
                found.extend(find_files_recursive(entry, extensions))
    except (PermissionError, OSError):
        pass
    return found

def extract_text(filepath: Path) -> str:
    try:
        with open(filepath, 'rb') as f:
            raw = f.read()
        for enc in ('utf-8', 'latin-1', 'cp1252', 'cp1251'):
            try: return raw.decode(enc)
            except UnicodeDecodeError: pass
        return raw.decode('utf-8', errors='ignore')
    except: return ""

def extract_emails_from_excel(filepath: Path) -> list:
    try:
        import pandas as pd
        df_dict = pd.read_excel(filepath, sheet_name=None, header=None)
        text = "\n".join(df.to_string(index=False, header=False) for df in df_dict.values())
        return EMAIL_RE.findall(text)
    except: return []

def extract_emails_from_csv(filepath: Path) -> list:
    emails = []
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            text = f.read()
        emails.extend(EMAIL_RE.findall(text))
        # Умный поиск колонок с email
        try:
            dialect = csv.Sniffer().sniff(text[:1024])
            reader = csv.reader(io.StringIO(text), dialect)
        except:
            reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        if rows:
            start_idx = 1 if not any('@' in c for c in rows[0]) else 0
            email_cols = set()
            for row in rows[start_idx:start_idx+10]:
                for i, cell in enumerate(row):
                    if '@' in cell: email_cols.add(i)
            for row in rows[start_idx:]:
                for i in email_cols:
                    if i < len(row):
                        emails.extend(EMAIL_RE.findall(row[i]))
    except: pass
    return emails

def extract_emails_from_archive(filepath: Path) -> list:
    try:
        import tempfile, shutil
        import patoolib
        temp_dir = tempfile.mkdtemp()
        try:
            patoolib.extract_archive(str(filepath), outdir=temp_dir, interactive=False)
            emails = []
            for root, _, files in os.walk(temp_dir):
                for name in files:
                    emails.extend(extract_emails_from_file(Path(root)/name))
            return emails
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    except: return []

def extract_emails_from_file(filepath: Path) -> list:
    ext = filepath.suffix.lower()
    if ext in ('.xlsx', '.xls'):
        return extract_emails_from_excel(filepath)
    elif ext == '.csv':
        return extract_emails_from_csv(filepath)
    elif ext in ('.zip', '.tar', '.gz', '.bz2', '.rar', '.7z'):
        return extract_emails_from_archive(filepath)
    else:
        return EMAIL_RE.findall(extract_text(filepath))

def process_local_files(directory: Path, all_contacts: dict) -> int:
    if not directory.exists():
        return 0
    files = find_files_recursive(directory, {"*"})
    new_total = 0
    with sync_tqdm(files, desc="📂 Локальные файлы", unit="файл") as pbar:
        for filepath in pbar:
            try:
                emails = extract_emails_from_file(filepath)
                for raw_email in emails:
                    e = raw_email.lower().strip()
                    if is_fake_email(e): continue
                    if e not in all_contacts:
                        all_contacts[e] = ("", "")
                        new_total += 1
            except: pass
    return new_total

# ---------- MX-ПРОВЕРКА С КЭШЕМ ----------
MX_CACHE = {}
MX_CACHE_LOCK = asyncio.Lock()

async def check_mx_cached(domain: str) -> bool:
    domain = domain.lower()
    if domain in MX_CACHE:
        return MX_CACHE[domain]
    async with MX_CACHE_LOCK:
        if domain in MX_CACHE:
            return MX_CACHE[domain]
        try:
            loop = asyncio.get_event_loop()
            answers = await loop.run_in_executor(None, dns.resolver.resolve, domain, 'MX')
            result = len(answers) > 0
        except:
            result = False
        MX_CACHE[domain] = result
        return result

# ---------- АСИНХРОННЫЕ СЕТЕВЫЕ ФУНКЦИИ ----------
async def fetch(client: httpx.AsyncClient, url: str) -> bytes | None:
    try:
        headers = {"User-Agent": ua.random}
        r = await client.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200: return None
        content = r.content
        if len(content) > MAX_MB * 1024 * 1024: return None
        if r.headers.get("content-encoding") == "gzip" or content[:2] == b'\x1f\x8b':
            try: content = gzip.decompress(content)
            except: pass
        return content
    except: return None

def decode(raw: bytes) -> str:
    for enc in ("utf-8", "latin-1", "cp1252"):
        try: return raw.decode(enc)
        except: pass
    return raw.decode("utf-8", errors="replace")

def parse_csv_auto(text: str) -> list:
    results = {}
    try:
        dialect = csv.Sniffer().sniff(text[:1024])
        reader = csv.reader(io.StringIO(text), dialect)
    except:
        reader = csv.reader(io.StringIO(text), delimiter=',')
    headers = []
    for rn, row in enumerate(reader):
        if not any(row): continue
        if rn == 0:
            headers = [h.lower().strip() for h in row]
            continue
        def col(kws):
            for kw in kws:
                for i, h in enumerate(headers):
                    if kw in h: return i
            return None
        ec = col(["email","e-mail","mail"])
        if ec is None:
            for cell in row:
                m = EMAIL_RE.search(cell)
                if m: results[m.group(0).lower()] = ("","")
            continue
        if ec >= len(row): continue
        m = EMAIL_RE.search(row[ec])
        if not m: continue
        e = m.group(0).lower()
        if is_fake_email(e): continue
        fc = col(["first","fname"]); lc = col(["last","lname"])
        first = row[fc] if fc is not None and fc < len(row) else ""
        last  = row[lc] if lc is not None and lc < len(row) else ""
        results[e] = (first.strip(), last.strip())
    return [(f,l,e) for e,(f,l) in results.items()]

def parse_generic(text: str) -> list:
    return [(f,l,e) for e,(f,l) in {email.lower(): ("","") for email in EMAIL_RE.findall(text) if not is_fake_email(email)}.items()]

async def extract_contacts(raw: bytes, url: str) -> list:
    low = url.lower()
    text = decode(raw)
    if "mc4wp" in low or "mailpoet-debug" in low:
        return parse_generic(text)
    if low.endswith(".csv") or "export=true" in low or "format=csv" in low:
        return parse_csv_auto(text)
    return parse_generic(text)

processed_count = 0
async def process_url(client, url, sem, all_contacts, checkpoint, pbar=None):
    global processed_count
    async with sem:
        if url in checkpoint["processed"]:
            return 0
        raw = await fetch(client, url)
        if not raw: return 0
        contacts = await extract_contacts(raw, url)
        new = 0
        for first, last, email in contacts:
            e = email.lower().strip()
            if not e or is_fake_email(e): continue
            domain = e.split('@')[1]
            if not await check_mx_cached(domain): continue
            if e not in all_contacts:
                all_contacts[e] = (first, last)
                new += 1
        if new:
            logging.getLogger(__name__).info(f"   🎯 +{new} адресов: {url[:70]}")
        checkpoint["processed"].append(url)
        processed_count += 1
        if pbar:
            pbar.update(1)
        return new

# ---------- PIPERMAIL (ПАРАЛЛЕЛЬНЫЙ ПОИСК С ПРОГРЕССОМ) ----------
async def discover_pipermail(client):
    servers = [
        "https://mail.python.org/pipermail/",
        "https://lists.ubuntu.com/archives/",
        "https://mail.gnome.org/archives/",
        "https://lists.freebsd.org/pipermail/",
        "https://lists.fedoraproject.org/archives/",
    ]

    async def process_server(base, progress_queue):
        """Обходит один сервер и кладёт найденные URL в очередь."""
        raw = await fetch(client, base)
        if not raw:
            return
        soup = BeautifulSoup(decode(raw), "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.endswith("/") and not href.startswith("?"):
                list_url = urllib.parse.urljoin(base, href)
                list_raw = await fetch(client, list_url)
                if not list_raw:
                    continue
                list_soup = BeautifulSoup(decode(list_raw), "html.parser")
                for month_a in list_soup.find_all("a", href=True):
                    month_href = month_a["href"]
                    if re.match(r'\d{4}-[A-Za-z]+/', month_href):
                        month_url = urllib.parse.urljoin(list_url, month_href)
                        month_raw = await fetch(client, month_url)
                        if not month_raw:
                            continue
                        month_soup = BeautifulSoup(decode(month_raw), "html.parser")
                        await progress_queue.put(month_url)
                        for file_a in month_soup.find_all("a", href=True):
                            file_href = file_a["href"]
                            if file_href.endswith((".html", ".htm")):
                                await progress_queue.put(urllib.parse.urljoin(month_url, file_href))

    log = logging.getLogger(__name__)
    log.info("🔎 Параллельный обход серверов Pipermail...")
    
    progress_queue = asyncio.Queue()
    tasks = [asyncio.create_task(process_server(srv, progress_queue)) for srv in servers]
    
    # Прогресс‑бар по количеству найденных URL
    pbar = async_tqdm(desc="🔍 Поиск страниц", unit="стр", position=0)
    found_count = 0
    # Ждём завершения всех серверов, одновременно забирая URL из очереди
    while tasks:
        done, pending = await asyncio.wait(tasks, timeout=0.5)
        # Отдаём все URL, которые уже есть в очереди
        while not progress_queue.empty():
            url = await progress_queue.get()
            yield url
            found_count += 1
            pbar.update(1)
        tasks = list(pending)
        if not pending:
            break
    # Забираем оставшиеся URL после завершения всех задач
    while not progress_queue.empty():
        url = await progress_queue.get()
        yield url
        found_count += 1
        pbar.update(1)
    pbar.close()
    log.info(f"📬 Всего найдено {found_count} страниц")

# ---------- GOOGLE DORKS ----------
EMAIL_DORKS = [
    'intitle:"index of" "subscribers.csv"',
    'intitle:"index of" "emails.csv"',
    'filetype:csv intext:"@" -intext:"example.com"',
    'filetype:txt intext:"@" -intext:"example.com"',
    'inurl:"wp-content/uploads/mc4wp-debug.log"',
    'intext:"@gmail.com" filetype:csv OR filetype:txt',
]

async def dork_google(query, max_results):
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, lambda: list(google_search(query, num_results=max_results, sleep_interval=DORK_SLEEP, lang="en", unique=True)))
    except: return []

async def dork_bing(client, query, max_results):
    urls, seen = [], set()
    for page in range(0, max_results, 10):
        try:
            r = await client.get("https://www.bing.com/search", params={"q": query, "first": page+1, "count": 10, "FORM": "PERE"})
            if r.status_code != 200: break
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.select("li.b_algo h2 a") or soup.select(".b_algo a[href]"):
                href = a.get("href")
                if href and href.startswith("http") and href not in seen:
                    seen.add(href); urls.append(href)
            await asyncio.sleep(random.uniform(1,2))
        except: break
    return urls[:max_results]

async def discover_via_dorks(client, known_urls):
    discovered, seen = [], known_urls.copy()
    for dork in EMAIL_DORKS:
        g_res, b_res = await asyncio.gather(
            dork_google(dork, DORK_RESULTS_PER_QUERY),
            dork_bing(client, dork, DORK_RESULTS_PER_QUERY),
            return_exceptions=True
        )
        for url in (g_res if isinstance(g_res, list) else []) + (b_res if isinstance(b_res, list) else []):
            if url not in seen:
                seen.add(url); discovered.append(url)
        await asyncio.sleep(DORK_SLEEP)
    return discovered

# ---------- GITHUB ----------
async def discover_github_emails(client):
    if not GITHUB_TOKEN: return []
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    emails = []
    for query in ["stars:>1000", "email list"]:
        for page in range(1, 4):
            r = await client.get("https://api.github.com/search/repositories", params={"q": query, "per_page": 30, "page": page}, headers=headers)
            if r.status_code != 200: break
            for repo in r.json().get("items", []):
                commits_url = repo["commits_url"].replace("{/sha}", "")
                r2 = await client.get(commits_url, headers=headers, params={"per_page": 30})
                if r2.status_code != 200: continue
                for commit in r2.json():
                    author = commit.get("commit", {}).get("author", {})
                    email = author.get("email", "")
                    if email and not is_fake_email(email) and not email.endswith("@users.noreply.github.com"):
                        fn, ln = split_name(author.get("name", ""))
                        emails.append((fn, ln, email.lower()))
                await asyncio.sleep(0.5)
    return emails

# ---------- ЗАГРУЗКА/СОХРАНЕНИЕ ----------
def load_existing():
    contacts = {}
    if CSV_OUTPUT.exists():
        with open(CSV_OUTPUT, encoding='utf-8') as f:
            reader = csv.reader(f); next(reader, None)
            for row in reader:
                if len(row) >= 3:
                    e = row[2].lower().strip()
                    if e and "@" in e and not is_fake_email(e):
                        contacts[e] = (row[0], row[1])
    return contacts

def save_results(contacts):
    with open(TXT_OUTPUT, "w", encoding='utf-8') as f:
        f.write("\n".join(sorted(contacts.keys())) + "\n")
    rows = sorted(((f,l,e) for e,(f,l) in contacts.items()), key=lambda x: x[2])
    with open(CSV_OUTPUT, "w", encoding='utf-8', newline='') as f:
        w = csv.writer(f); w.writerow(["Имя","Фамилия","Email"]); w.writerows(rows)

# ---------- MAIN ----------
async def main():
    global processed_count
    start_time = datetime.now()
    log = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")

    log.info("============================================================")
    log.info("🚀 ЗАПУСК EMAIL EXTRACTOR v12.0 FINAL — MAXIMUM OVERDRIVE")
    log.info("============================================================")

    all_contacts = load_existing()
    checkpoint = {"processed": []}
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE) as f:
            checkpoint = json.load(f)
        log.info(f"⏯ Продолжаем с контрольной точки. Обработано: {len(checkpoint['processed'])}")

    # Фаза 0: Локальные файлы
    log.info("\n📂 ЭТАП 0: Локальное сканирование")
    phase_start = datetime.now()
    before_local = len(all_contacts)
    await asyncio.to_thread(process_local_files, LOCAL_SCAN_DIR, all_contacts)
    log.info(f"✅ Локальные файлы добавили {len(all_contacts)-before_local} адресов. Этап занял {format_time((datetime.now()-phase_start).total_seconds())}")

    sem = asyncio.Semaphore(MAX_CONCURRENT)
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=50)
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, limits=limits, follow_redirects=True, verify=False, http2=True) as client:
        # Фаза 1: Pipermail
        log.info("\n📧 ЭТАП 1: Архивы Pipermail")
        phase_start = datetime.now()
        tasks = []
        # Прогресс-бар без total – показывает только количество обработанных
        pbar = async_tqdm(desc="Обработка страниц", unit="стр", position=0, total=None)
        async for url in discover_pipermail(client):
            if url not in checkpoint["processed"]:
                task = asyncio.create_task(process_url(client, url, sem, all_contacts, checkpoint, pbar))
                tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks)
        pbar.close()
        log.info(f"✅ Обработано {len(tasks)} страниц Pipermail. Этап занял {format_time((datetime.now()-phase_start).total_seconds())}")

        # Фаза 2: Dork Discovery
        log.info("\n🔍 ЭТАП 2: Google Dorks")
        phase_start = datetime.now()
        dork_urls = await discover_via_dorks(client, set(checkpoint["processed"]))
        dork_urls = [u for u in dork_urls if u not in checkpoint["processed"]]
        pbar = async_tqdm(total=len(dork_urls), desc="Обработка Dorks", unit="URL", position=0)
        tasks = [process_url(client, url, sem, all_contacts, checkpoint, pbar) for url in dork_urls]
        await asyncio.gather(*tasks)
        pbar.close()
        log.info(f"✅ Обработано {len(dork_urls)} URL из Dorks. Этап занял {format_time((datetime.now()-phase_start).total_seconds())}")

        # Фаза 3: GitHub
        log.info("\n🐙 ЭТАП 3: GitHub")
        phase_start = datetime.now()
        gh_emails = await discover_github_emails(client)
        added = 0
        for fn, ln, email in gh_emails:
            if email not in all_contacts:
                domain = email.split('@')[1]
                if await check_mx_cached(domain):
                    all_contacts[email] = (fn, ln)
                    added += 1
        log.info(f"✅ GitHub добавил {added} адресов. Этап занял {format_time((datetime.now()-phase_start).total_seconds())}")

        # Сохраняем чекпоинт
        with open(CHECKPOINT_FILE, "w") as f:
            json.dump(checkpoint, f)

    # Итоги
    save_results(all_contacts)
    total_elapsed = (datetime.now() - start_time).total_seconds()
    log.info("============================================================")
    log.info(f"🏁 РАБОТА ЗАВЕРШЕНА за {format_time(total_elapsed)}")
    log.info(f"📊 ВСЕГО УНИКАЛЬНЫХ EMAIL: {len(all_contacts)}")
    log.info("============================================================")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("🛑 Прервано пользователем.")