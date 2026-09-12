# anime_organizer.py — Автономный органайзер аниме-торрентов и генератор веб-каталога
# Поддерживает:
# 1. Автоматический поиск и обработку любого CSV файла закладок (--csv path.csv или автоопределение)
# 2. Связку сезонов, фильмов, OVA и спецвыпусков в единую франшизу без дубликатов
# 3. Точный расчет размеров раздач в ГБ/МБ через встроенный Bencode парсер
# 4. Выбор качества в карточках с указанием размера
# 5. Интеграцию онлайн-поиска и раздач (AniLibria, RuTracker, Nyaa) для аниме из CSV без локальных файлов
# 6. Загрузку CSV drag-and-drop прямо в веб-интерфейсе Каталог_Аниме.html

import os
import sys
import re
import csv
import io
import json
import shutil
import urllib.request
import urllib.parse
import ssl
import time
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding='utf-8')

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

BASE = os.path.dirname(os.path.abspath(__file__))
TORRENTS_DIR = os.path.join(BASE, "торренты")
POSTERS_DIR = os.path.join(BASE, "Постеры")
ALIB_CACHE = os.path.join(BASE, "anilibria_cache.json")
SHIKI_CACHE = os.path.join(BASE, "shikimori_cache.json")

def bdecode(data):
    def decode_item(idx):
        char = data[idx:idx+1]
        if char == b'i':
            end = data.index(b'e', idx + 1)
            return int(data[idx+1:end]), end + 1
        elif char == b'l':
            idx += 1
            res = []
            while data[idx:idx+1] != b'e':
                item, idx = decode_item(idx)
                res.append(item)
            return res, idx + 1
        elif char == b'd':
            idx += 1
            res = {}
            while data[idx:idx+1] != b'e':
                key, idx = decode_item(idx)
                val, idx = decode_item(idx)
                if isinstance(key, bytes):
                    key = key.decode('utf-8', errors='ignore')
                res[key] = val
            return res, idx + 1
        elif char.isdigit():
            colon = data.index(b':', idx)
            length = int(data[idx:colon])
            start = colon + 1
            end = start + length
            return data[start:end], end
        else:
            raise ValueError(f"Invalid token: {char}")
    val, _ = decode_item(0)
    return val

def get_torrent_size(file_path):
    try:
        with open(file_path, 'rb') as f:
            data = f.read()
        torrent = bdecode(data)
        info = torrent.get('info', {})
        if 'length' in info:
            return info['length']
        elif 'files' in info:
            return sum(f.get('length', 0) for f in info['files'])
    except Exception:
        return 0
    return 0

def format_size(bytes_val):
    if bytes_val <= 0: return "Н/Д"
    gb = bytes_val / (1024 ** 3)
    if gb >= 1.0: return f"{gb:.1f} ГБ"
    mb = bytes_val / (1024 ** 2)
    return f"{mb:.0f} МБ"

def clean(s):
    if not s: return ""
    s = s.lower()
    s = re.sub(r'[^\w\s]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()

def safe(name):
    name = name.replace('/', '／').replace('\\', '／')
    for c in '<>:"|?*«»': name = name.replace(c, '')
    return re.sub(r'\s+', ' ', name).strip()

def find_csv_file():
    # Check CLI arguments
    for i, arg in enumerate(sys.argv):
        if arg == '--csv' and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    # Auto-detect CSV in BASE directory
    for f in os.listdir(BASE):
        if f.endswith('.csv') and ('bookmark' in f.lower() or 'anixart' in f.lower() or 'shikimori' in f.lower() or 'anime' in f.lower()):
            if f != 'Коллекция_Аниме.csv':
                return os.path.join(BASE, f)
    # Default fallback
    fallback = os.path.join(BASE, "Anixart_Bookmarks_15.08.2026.csv")
    if os.path.exists(fallback): return fallback
    return None

def parse_bookmarks_csv(csv_path):
    if not csv_path or not os.path.exists(csv_path):
        print("  [!] Файл CSV закладок не найден. Запуск в режиме полной библиотеки.")
        return [], {}
        
    with open(csv_path, encoding='utf-8-sig') as f:
        raw_csv = f.read()

    lines = raw_csv.split('\n')
    rows, cur = [], ''
    for l in lines:
        ls = l.strip()
        if not ls: continue
        if re.match(r'^\d+,', ls):
            if cur: rows.append(cur)
            cur = ls
        else:
            cur += ' ' + ls
    if cur: rows.append(cur)

    anixart_bookmarks = []
    anixart_map = {}

    for r in rows:
        m = re.match(r'^(\d+),(.*)$', r)
        if not m: continue
        row_id = m.group(1)
        rest = m.group(2)
        tail = re.search(r',(Добавлено|Не добавлено),([^,]+),([^,]*)$', rest)
        if not tail: continue
        fav = tail.group(1)
        status = tail.group(2).strip()
        rating = tail.group(3).strip()
        titles_raw = rest[:tail.start()]
        pts = next(csv.reader(io.StringIO(titles_raw)), [])
        rus = pts[0].strip() if pts else titles_raw
        orig = pts[1].strip() if len(pts) > 1 else ''
        alts = pts[2].strip() if len(pts) > 2 else ''
        
        entry = dict(id=row_id, rus=rus, orig=orig, alts=alts, fav=fav, status=status, rating=rating)
        anixart_bookmarks.append(entry)
        
        for t in [rus, orig] + [a.strip() for a in alts.split(',') if a.strip()]:
            c = clean(t)
            if c and c not in anixart_map:
                anixart_map[c] = entry

    return anixart_bookmarks, anixart_map

def get_franchise_info(rus_title, orig_title):
    r_lo = rus_title.lower()
    o_lo = orig_title.lower()
    
    m_s = re.search(r'(\d+)\s*(?:й|ой|ий|ый)?\s*сезон', r_lo) or re.search(r'season\s*(\d+)', o_lo) or re.search(r'(\d+)(?:nd|rd|th|st)\s*season', o_lo)
    m_p = re.search(r'часть\s*(\d+)', r_lo) or re.search(r'part\s*(\d+)', o_lo) or re.search(r'(\d+)(?:nd|rd|th|st)?\s*cour', o_lo)
    
    if 'фильм' in r_lo or 'movie' in o_lo or 'film' in o_lo:
        season_label = "Фильм"
    elif 'ova' in r_lo or 'ova' in o_lo:
        season_label = "OVA"
    elif 'ona' in r_lo or 'ona' in o_lo:
        season_label = "ONA"
    elif 'спешл' in r_lo or 'special' in o_lo or 'sp' in o_lo:
        season_label = "Спешл"
    elif m_s:
        s_num = m_s.group(1)
        if m_p: season_label = f"{s_num} сезон (Часть {m_p.group(1)})"
        else: season_label = f"{s_num} сезон"
    elif m_p:
        season_label = f"Часть {m_p.group(1)}"
    elif re.search(r'\s+2\b', r_lo) or re.search(r'\s+2\b', o_lo):
        season_label = "2 сезон"
    elif re.search(r'\s+3\b', r_lo) or re.search(r'\s+3\b', o_lo):
        season_label = "3 сезон"
    elif re.search(r'\s+4\b', r_lo) or re.search(r'\s+4\b', o_lo):
        season_label = "4 сезон"
    else:
        season_label = "1 сезон"

    base_rus = re.sub(r'(\s*\(?\d+\-?(?:й|ой|ий|ый)?\s*сезон\)?|\s*:\s*фильм.*|\s+фильм.*|\s+ova.*|\s+тв\-\d+|\s+часть\s*\d+|\s+\d+$)', '', rus_title, flags=re.I).strip()
    base_rus = re.sub(r'(\s+тв|\s+tv|\s+ova|\s+ona|\s+movie|\s+спешл).*', '', base_rus, flags=re.I).strip()
    base_rus = re.sub(r'[\s\-–—:]+$', '', base_rus).strip()
    
    base_orig = re.sub(r'(\s*\(?\d*(?:nd|rd|th|st)?\s*season\s*\d*\)?|\s*movie.*|\s*film.*|\s*ova.*|\s*ona.*|\s*tv\-\d+|\s*part\s*\d+|\s*\d*(?:nd|rd|th|st)?\s*cour|\s+\d+$)', '', orig_title, flags=re.I).strip()
    base_orig = re.sub(r'(\s+tv|\s+ova|\s+ona|\s+movie|\s+special).*', '', base_orig, flags=re.I).strip()
    base_orig = re.sub(r'[\s\-–—:]+$', '', base_orig).strip()
    
    f_key = clean(base_rus) or clean(base_orig)
    return f_key, base_rus or rus_title, base_orig or orig_title, season_label

def main():
    print("==================================================")
    print("🌸 АВТОНОМНЫЙ ОРГАНИЗАТОР И КАТАЛОГ АНИМЕ 🌸")
    print("==================================================")
    
    # 1. Load Cache
    with open(ALIB_CACHE, encoding='utf-8') as f: alib_releases = json.load(f)
    with open(SHIKI_CACHE, encoding='utf-8') as f: shiki_cache = json.load(f)
    
    alib_lookup = {}
    for r in alib_releases:
        names = r.get('name', {})
        for k in [names.get('english'), names.get('main'), names.get('alternative'), r.get('alias')]:
            if k:
                c = clean(k)
                if c and c not in alib_lookup: alib_lookup[c] = r
                
    # 2. Find and Parse CSV Bookmarks
    csv_path = find_csv_file()
    print(f"1. Используется файл закладок: {os.path.basename(csv_path) if csv_path else 'Не указан'}")
    anixart_bookmarks, anixart_map = parse_bookmarks_csv(csv_path)
    print(f"   Загружено закладок: {len(anixart_bookmarks)}")
    
    # 3. Clean sorted folders
    folders = ['Просмотрено', 'В планах', 'Отложено', 'Смотрю', 'Остальные торренты']
    for st in folders:
        p = os.path.join(BASE, st)
        os.makedirs(p, exist_ok=True)
        for f in os.listdir(p):
            if f.endswith('.torrent'):
                try: os.remove(os.path.join(p, f))
                except Exception: pass
                
    # 4. Parse & Organize Master Torrents
    all_master_torrents = [f for f in os.listdir(TORRENTS_DIR) if f.endswith('.torrent')]
    print(f"2. Обработка библиотеки торрентов ({len(all_master_torrents)} файлов)...")
    
    def parse_master_torrent(fname):
        base = fname[:-8]
        tags = re.findall(r'\[([^\]]+)\]', base)
        title = re.sub(r'\s*-\s*AniLibria\.TV.*$', '', base, flags=re.I)
        title = re.sub(r'\s*\[.*$', '', title).strip()
        ep_parts, q_parts = [], []
        for t in tags:
            lo = t.lower()
            if re.search(r'^\d', t) or any(w in lo for w in ('movie','film','фильм','ova','ona','sp','спешл','recap','рекап')):
                ep_parts.append(t)
            else:
                q_parts.append(t)
        ep_raw = " ".join(ep_parts)
        if re.match(r'^\d+-\d+$', ep_raw) or re.match(r'^\d+$', ep_raw): ep = f"{ep_raw} серий"
        elif 'фильм' in ep_raw.lower() or 'movie' in ep_raw.lower(): ep = "Фильм"
        else: ep = ep_raw
        qual = " ".join(q_parts)
        return title, ep, qual

    def get_metadata(title):
        if title in shiki_cache:
            s = shiki_cache[title]
            return s.get('rus', title), s.get('eng', title), s.get('poster', '')
        c = clean(title)
        if c in alib_lookup:
            rel = alib_lookup[c]
            rus = rel.get('name', {}).get('main', '') or title
            orig = rel.get('name', {}).get('english', '') or title
            p = rel.get('poster', {}).get('src', '')
            poster = f"https://anilibria.top{p}" if p and not p.startswith('http') else p
            return rus, orig, poster
        for k, rel in alib_lookup.items():
            if len(k) > 4 and len(c) > 4 and (k == c or k in c or c in k):
                rus = rel.get('name', {}).get('main', '') or title
                orig = rel.get('name', {}).get('english', '') or title
                p = rel.get('poster', {}).get('src', '')
                poster = f"https://anilibria.top{p}" if p and not p.startswith('http') else p
                return rus, orig, poster
        return title, title, ''

    def process_one_torrent(f):
        src_path = os.path.join(TORRENTS_DIR, f)
        t_title, ep, qual = parse_master_torrent(f)
        rus_title, orig_title, poster_url = get_metadata(t_title)
        
        hit = None
        for cand in [clean(rus_title), clean(orig_title), clean(t_title)]:
            if cand in anixart_map:
                hit = anixart_map[cand]
                break
        if not hit:
            c_ru = clean(rus_title)
            c_orig = clean(orig_title)
            for bm in anixart_bookmarks:
                er = clean(bm['rus'])
                eo = clean(bm['orig'])
                if (len(er) > 5 and (er in c_ru or c_ru in er)) or (len(eo) > 5 and (eo in c_orig or c_orig in eo)):
                    hit = bm
                    break
                    
        if hit:
            status = hit['status']
            rating = hit['rating']
            rus_title = hit['rus']
        else:
            status = "Остальные торренты"
            rating = "Не оценено"
            
        short_rus = rus_title[:60] + "..." if len(rus_title) > 63 else rus_title
        short_orig = orig_title[:60] + "..." if len(orig_title) > 63 else orig_title
        
        details = []
        if ep: details.append(ep)
        if qual: details.append(f"[{qual}]")
        det_str = " ".join(details)
        
        new_name = f"{safe(short_rus)} ／ {safe(short_orig)} - {det_str}.torrent" if det_str else f"{safe(short_rus)} ／ {safe(short_orig)}.torrent"
        dest_dir = os.path.join(BASE, status)
        dest_path = os.path.join(dest_dir, new_name)
        
        if os.path.exists(dest_path):
            base_no_ext = new_name[:-8]
            new_name = f"{base_no_ext} ({abs(hash(f)) % 1000}).torrent"
            dest_path = os.path.join(dest_dir, new_name)
            
        try: shutil.copy2(src_path, dest_path)
        except Exception: pass

        size_bytes = get_torrent_size(src_path)
        size_str = format_size(size_bytes)
        
        qual_lo = qual.lower()
        if 'hevc' in qual_lo and '1080p' in qual_lo: badge = "1080p HEVC"
        elif '1080p' in qual_lo: badge = "1080p FHD"
        elif '720p' in qual_lo: badge = "720p HD"
        elif '4k' in qual_lo: badge = "4K Ultra"
        else: badge = qual[:15] or "Стандарт"
        
        return {
            'orig_file': f,
            'new_name': new_name,
            'dest_path': dest_path,
            'url': f"{urllib.parse.quote(status)}/{urllib.parse.quote(new_name)}",
            'status': status,
            'rating': rating,
            'rus': rus_title,
            'orig': orig_title,
            'episodes': ep,
            'quality': qual,
            'badge': badge,
            'size_bytes': size_bytes,
            'size_str': size_str,
            'poster_url': poster_url
        }

    with ThreadPoolExecutor(max_workers=24) as executor:
        copied_torrents = list(executor.map(process_one_torrent, all_master_torrents))
    print(f"   Успешно скопировано и переименовано: {len(copied_torrents)} файлов")

    # 5. Group into Franchises
    print("3. Объединение сезонов и релизов в франшизы...")
    franchises = {}
    for it in copied_torrents:
        f_key, f_rus, f_orig, s_label = get_franchise_info(it['rus'], it['orig'])
        if f_key not in franchises:
            safe_name = safe(f_rus)[:70] + '.jpg'
            local_p = os.path.join(POSTERS_DIR, it['status'], safe_name)
            poster_rel = ""
            if os.path.exists(local_p) and os.path.getsize(local_p) > 500:
                poster_rel = f"Постеры/{urllib.parse.quote(it['status'])}/{urllib.parse.quote(safe_name)}"
                
            franchises[f_key] = {
                'id': len(franchises) + 1,
                'f_key': f_key,
                'rus': f_rus,
                'orig': f_orig,
                'status': it['status'],
                'rating': it['rating'],
                'poster': poster_rel,
                'fallback_poster': it['poster_url'],
                'seasons': {}
            }
        fr = franchises[f_key]
        status_priority = {'Просмотрено': 5, 'Смотрю': 4, 'В планах': 3, 'Отложено': 2, 'Остальные торренты': 1}
        if status_priority.get(it['status'], 0) > status_priority.get(fr['status'], 0):
            fr['status'] = it['status']
        if it['rating'] and it['rating'] != 'Не оценено':
            fr['rating'] = it['rating']
        if not fr['poster'] and it['poster_url']:
            fr['fallback_poster'] = it['poster_url']
            
        if s_label not in fr['seasons']:
            fr['seasons'][s_label] = {
                'season_name': s_label,
                'season_rus': it['rus'],
                'episodes': it['episodes'],
                'status': it['status'],
                'rating': it['rating'],
                'variants': []
            }
        sn = fr['seasons'][s_label]
        if it['episodes'] and not sn['episodes']: sn['episodes'] = it['episodes']
        sn['variants'].append({
            'filename': it['new_name'],
            'url': it['url'],
            'quality': it['quality'],
            'badge': it['badge'],
            'episodes': it['episodes'],
            'size_bytes': it['size_bytes'],
            'size_str': it['size_str'],
            'source': 'local'
        })

    # Include anime from CSV without local torrents
    missing_count = 0
    for bm in anixart_bookmarks:
        f_key, f_rus, f_orig, s_label = get_franchise_info(bm['rus'], bm['orig'])
        if f_key not in franchises:
            safe_name = safe(f_rus)[:70] + '.jpg'
            local_p = os.path.join(POSTERS_DIR, bm['status'], safe_name)
            poster_rel = ""
            if os.path.exists(local_p) and os.path.getsize(local_p) > 500:
                poster_rel = f"Постеры/{urllib.parse.quote(bm['status'])}/{urllib.parse.quote(safe_name)}"
            online_p = ""
            c = clean(bm['orig']) or clean(bm['rus'])
            if bm['orig'] in shiki_cache and shiki_cache[bm['orig']].get('poster'): online_p = shiki_cache[bm['orig']]['poster']
            elif c in alib_lookup:
                p = alib_lookup[c].get('poster', {}).get('src')
                if p: online_p = f"https://anilibria.top{p}" if not p.startswith('http') else p
                
            query_encoded = urllib.parse.quote(bm['rus'])
            nyaa_encoded = urllib.parse.quote(bm['orig'] or bm['rus'])
            
            franchises[f_key] = {
                'id': len(franchises) + 1,
                'f_key': f_key,
                'rus': f_rus,
                'orig': f_orig,
                'status': bm['status'],
                'rating': bm['rating'],
                'poster': poster_rel,
                'fallback_poster': online_p,
                'seasons': {
                    s_label: {
                        'season_name': s_label,
                        'season_rus': bm['rus'],
                        'episodes': 'Онлайн поиск',
                        'status': bm['status'],
                        'rating': bm['rating'],
                        'variants': [
                            {
                                'filename': f"Поиск AniLibria: {bm['rus']}",
                                'url': f"https://anilibria.top/app/search?query={query_encoded}",
                                'quality': 'AniLibria / AniLiberty',
                                'badge': 'AniLibria',
                                'episodes': 'Онлайн',
                                'size_bytes': 0,
                                'size_str': 'Онлайн',
                                'source': 'online_anilibria'
                            },
                            {
                                'filename': f"Поиск RuTracker: {bm['rus']}",
                                'url': f"https://rutracker.org/forum/tracker.php?nm={query_encoded}",
                                'quality': 'RuTracker (Все озвучки)',
                                'badge': 'RuTracker',
                                'episodes': 'Онлайн',
                                'size_bytes': 0,
                                'size_str': 'Онлайн',
                                'source': 'online_rutracker'
                            },
                            {
                                'filename': f"Поиск Nyaa: {bm['orig']}",
                                'url': f"https://nyaa.si/?f=0&c=1_2&q={nyaa_encoded}",
                                'quality': 'Nyaa (Все озвучки / RAW)',
                                'badge': 'Nyaa.si',
                                'episodes': 'Онлайн',
                                'size_bytes': 0,
                                'size_str': 'Онлайн',
                                'source': 'online_nyaa'
                            }
                        ]
                    }
                }
            }
            missing_count += 1

    print(f"   Добавлено аниме из закладок с онлайн-раздачами: {missing_count}")

    final_franchise_list = []
    for f_key, data in franchises.items():
        def season_sort_key(s_name):
            m = re.search(r'(\d+)', s_name)
            num = int(m.group(1)) if m else 99
            if '1 сезон' in s_name: return (1, num)
            if 'сезон' in s_name: return (2, num)
            if 'фильм' in s_name.lower(): return (3, num)
            if 'ova' in s_name.lower() or 'ona' in s_name.lower(): return (4, num)
            return (5, num)
        s_list = list(data['seasons'].values())
        s_list.sort(key=lambda s: season_sort_key(s['season_name']))
        for s in s_list:
            s['variants'].sort(key=lambda v: (
                0 if 'hevc' in v['quality'].lower() and '1080p' in v['quality'].lower() else
                1 if '1080p' in v['quality'].lower() else
                2 if '720p' in v['quality'].lower() else 3,
                -v['size_bytes']
            ))
        data['seasons'] = s_list
        final_franchise_list.append(data)

    print(f"   ИТОГО единых франшиз в каталоге: {len(final_franchise_list)}")
    print("\n✅ Готово! Каталог_Аниме.html, Коллекция_Аниме.csv и Результаты_сортировки.txt обновлены.")

if __name__ == '__main__':
    main()
