import urllib.request
import os
import json
from pathlib import Path

# Ссылки из тех репозиториев, которые ты скинул
SOURCES = [
    # Главный и самый большой репозиторий (disposable-email-domains)
    "https://raw.githubusercontent.com/disposable-email-domains/disposable-email-domains/master/disposable_email_blocklist.conf",
    # Дополнительные списки из других NPM-пакетов и репозиториев
    "https://raw.githubusercontent.com/FGRibreau/mailchecker/master/list.txt",
    "https://raw.githubusercontent.com/martenson/disposable-email-domains/master/disposable_email_blocklist.conf"
]

DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"
OUTPUT_FILE = DATA_DIR / "disposable_blocklist.txt"

def update_blocklist():
    print("Starting download of the latest disposable domains...")
    DATA_DIR.mkdir(exist_ok=True)
    
    all_domains = set()
    
    for url in SOURCES:
        try:
            print(f"Downloading: {url}")
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                content = response.read().decode('utf-8')
                for line in content.splitlines():
                    domain = line.strip().lower()
                    if domain and not domain.startswith('#') and '.' in domain:
                        all_domains.add(domain)
        except Exception as e:
            print(f"Error downloading {url}: {e}")
            
    print(f"Total unique disposable domains collected: {len(all_domains)}")
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        for d in sorted(all_domains):
            f.write(f"{d}\n")
            
    print(f"Database successfully saved to {OUTPUT_FILE}")

if __name__ == "__main__":
    update_blocklist()
