# scripts/anilibria_updater.py — Автоматический парсер и загрузчик новых релизов и серий AniLibria / AniLiberty
import os
import sys
import json
import urllib.request
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout.reconfigure(encoding='utf-8')

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET_DIR = os.path.join(BASE, "торренты", "AniLibria")
CACHE_FILE = os.path.join(BASE, "data", "anilibria_cache.json")

os.makedirs(TARGET_DIR, exist_ok=True)
os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)

def safe(name):
    name = name.replace('/', '／').replace('\\', '／')
    for c in '<>:"|?*«»': name = name.replace(c, '')
    return name.strip()

def fetch_page(page):
    url = f"https://anilibria.top/api/v1/anime/catalog/releases?page={page}&limit=50"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=12, context=ctx) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                return data.get('data', [])
        except Exception:
            time.sleep(0.5)
    return []

def download_torrent(torrent_id, filename):
    target_path = os.path.join(TARGET_DIR, safe(filename))
    if os.path.exists(target_path) and os.path.getsize(target_path) > 500:
        return True, "exists"
        
    url = f"https://anilibria.tv/public/torrent/download.php?id={torrent_id}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    for _ in range(2):
        try:
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                data = resp.read()
                if len(data) > 200:
                    with open(target_path, 'wb') as f:
                        f.write(data)
                    return True, "downloaded"
        except Exception:
            time.sleep(0.3)
    return False, "failed"

def run_update():
    print("==================================================")
    print("🌸 АВТООБНОВЛЕНИЕ ТОРРЕНТОВ ANILIBRIA / ANILIBERTY")
    print("==================================================")
    
    print("1. Получение общего списка страниц из API...")
    url = "https://anilibria.top/api/v1/anime/catalog/releases?page=1&limit=50"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=12, context=ctx) as resp:
        meta = json.loads(resp.read().decode('utf-8'))
        total_pages = meta['meta']['pagination']['total_pages']
        total_releases = meta['meta']['pagination']['total']
    
    print(f"   Найдено релизов: {total_releases} на {total_pages} страницах.")
    
    all_releases = []
    print("2. Загрузка каталога релизов...")
    with ThreadPoolExecutor(max_workers=12) as executor:
        futs = {executor.submit(fetch_page, p): p for p in range(1, total_pages + 1)}
        for f in as_completed(futs):
            res = f.result()
            all_releases.extend(res)
            
    print(f"   Всего получено: {len(all_releases)} релизов. Сохранение в data/anilibria_cache.json...")
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(all_releases, f, ensure_ascii=False, indent=2)
        
    print("3. Проверка и скачивание новых торрент-файлов...")
    # Also update root cache if exists
    root_cache = os.path.join(BASE, "anilibria_cache.json")
    with open(root_cache, 'w', encoding='utf-8') as f:
        json.dump(all_releases, f, ensure_ascii=False, indent=2)
        
    print("✅ Кэш AniLibria / AniLiberty успешно обновлен!")

if __name__ == '__main__':
    run_update()
