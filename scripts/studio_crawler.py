# scripts/studio_crawler.py — Мульти-студийный парсер и индексер озвучек
# Поддерживает студии: Dream Cast, SHIZA Project, AnimeVost, Studio Band, AniDUB, Nyaa.si
# Скачивает и индексирует торренты строго по отдельным папкам: торренты/{Studio}/

import os
import sys
import json
import re
import urllib.request
import urllib.parse
import ssl
import time
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding='utf-8')

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TORRENTS_BASE = os.path.join(BASE, "торренты")
STUDIOS_DATA = os.path.join(BASE, "data", "studios_index.json")

STUDIO_CONFIGS = {
    "DreamCast": {
        "name": "Dream Cast",
        "badge": "Dream Cast",
        "icon": "🌟",
        "dir": os.path.join(TORRENTS_BASE, "DreamCast"),
        "search_url": "https://animego.org/search/anime?q={query}"
    },
    "SHIZA_Project": {
        "name": "SHIZA Project",
        "badge": "SHIZA Project",
        "icon": "⚡",
        "dir": os.path.join(TORRENTS_BASE, "SHIZA_Project"),
        "search_url": "https://animego.org/search/anime?q={query}"
    },
    "AnimeVost": {
        "name": "AnimeVost",
        "badge": "AnimeVost",
        "icon": "🎙️",
        "dir": os.path.join(TORRENTS_BASE, "AnimeVost"),
        "search_url": "https://animevost.org/index.php?do=search&subaction=search&story={query}"
    },
    "StudioBand": {
        "name": "Studio Band",
        "badge": "Studio Band",
        "icon": "🎭",
        "dir": os.path.join(TORRENTS_BASE, "StudioBand"),
        "search_url": "https://animego.org/search/anime?q={query}"
    },
    "AniDUB": {
        "name": "AniDUB",
        "badge": "AniDUB",
        "icon": "🎧",
        "dir": os.path.join(TORRENTS_BASE, "AniDUB"),
        "search_url": "https://animego.org/search/anime?q={query}"
    },
    "MultiDub_Sub": {
        "name": "Все озвучки / Субтитры (Nyaa)",
        "badge": "Nyaa.si",
        "icon": "🌐",
        "dir": os.path.join(TORRENTS_BASE, "MultiDub_Sub"),
        "search_url": "https://nyaa.si/?f=0&c=1_2&q={query}"
    }
}

for s_key, conf in STUDIO_CONFIGS.items():
    os.makedirs(conf['dir'], exist_ok=True)

def safe(name):
    name = name.replace('/', '／').replace('\\', '／')
    for c in '<>:"|?*«»': name = name.replace(c, '')
    return name.strip()

def search_nyaa_releases(query):
    """Поиск релизов с английскими/русскими субтитрами и дубляжом на Nyaa"""
    clean_q = re.sub(r'[^\w\s]', ' ', query).strip()
    q = urllib.parse.quote(clean_q)
    url = f"https://nyaa.si/?page=rss&q={q}&c=1_2&f=0"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    releases = []
    try:
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            content = resp.read().decode('utf-8', errors='ignore')
            # parse xml items
            items = re.findall(r'<item>(.*?)</item>', content, re.DOTALL)
            for it in items[:5]:
                t_m = re.search(r'<title>(.*?)</title>', it)
                l_m = re.search(r'<link>(.*?)</link>', it)
                s_m = re.search(r'<nyaa:size>(.*?)</nyaa:size>', it)
                if t_m and l_m:
                    title = t_m.group(1).replace('<![CDATA[', '').replace(']]>', '').strip()
                    link = l_m.group(1).strip()
                    size = s_m.group(1).strip() if s_m else "Онлайн"
                    releases.append({
                        'title': title,
                        'url': link,
                        'size_str': size,
                        'source': 'nyaa'
                    })
    except Exception:
        pass
    return releases

def generate_studios_index():
    print("==================================================")
    print("🎙️ ИНДЕКСАЦИЯ СТУДИЙ ОЗВУЧКИ И ТОРРЕНТ-ТРЕКЕРОВ")
    print("==================================================")
    
    studios_index = {}
    for s_key, conf in STUDIO_CONFIGS.items():
        # Scan local files
        local_files = [f for f in os.listdir(conf['dir']) if f.endswith('.torrent')]
        studios_index[s_key] = {
            'name': conf['name'],
            'badge': conf['badge'],
            'icon': conf['icon'],
            'folder': f"торренты/{s_key}",
            'local_torrents_count': len(local_files),
            'search_template': conf['search_url']
        }
        print(f"  • [{conf['name']}]: {len(local_files)} локальных торрентов в папке торренты/{s_key}/")

    os.makedirs(os.path.dirname(STUDIOS_DATA), exist_ok=True)
    with open(STUDIOS_DATA, 'w', encoding='utf-8') as f:
        json.dump(studios_index, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Индекс студий озвучки сохранен в data/studios_index.json")

if __name__ == '__main__':
    generate_studios_index()
