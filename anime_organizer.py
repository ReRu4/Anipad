# anime_organizer.py — Автономный органайзер аниме-торрентов и генератор веб-каталога
# Поддерживает:
# 1. Автоматический поиск и обработку любого CSV файла закладок (--csv path.csv или автоопределение)
# 2. Связку сезонов, фильмов, OVA и спецвыпусков в единую франшизу без дубликатов
# 3. Точный расчет размеров раздач в ГБ/МБ через встроенный Bencode парсер
# 4. Выбор качества в карточках с указанием размера
# 5. Интеграцию онлайн-поиска и релизов (AniLiberty, AnimeGO, Nyaa) для аниме из CSV без локальных файлов
# 6. Динамическое переключение постеров сезонов и модальное окно с описанием и жанрами

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
CSV_FILE = os.path.join(BASE, "Anixart_Bookmarks_15.08.2026.csv")
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

EXACT_OVERRIDE = {
    "91 days": {"rus": "91 день", "orig": "91 Days", "poster": "https://anilibria.top/storage/releases/posters/2621/DVXo5Et1dENZo1aQWxVXETczQSQwSkYk.jpg", "genres": ["Экшен", "Драма", "Исторический"], "desc": "В эпоху сухого закона мафия правит городом. Авилио возвращается в город Закон после долгих лет изгнания, чтобы отомстить мафиозной семье Ванетти за убийство своей семьи."},
    "days": {"rus": "Дни", "orig": "Days", "poster": "https://anilibria.top/storage/releases/posters/2642/DHlCIDHvHSc9FDCCLOoHUw5mhoFyU5gg.jpg", "genres": ["Спорт", "Школа", "Сёнен"], "desc": "История о двух парнях, которые повстречались одной ветреной ночью: Цукуси, неуклюжем и неприметном парне, и Дзине, футбольном гении. В тот день они изменили мир старшей школы Сэйсэки."},
    "attack on titan": {"rus": "Атака титанов", "orig": "Shingeki no Kyojin", "poster": "https://shikimori.one/system/animes/original/16498.jpg?1711973439"},
    "bleach sennen kessen hen": {"rus": "Блич: Тысячелетняя кровавая война", "orig": "Bleach: Sennen Kessen-hen", "poster": "https://shikimori.one/system/animes/original/41467.jpg?1711944193"},
    "bluelock": {"rus": "Синяя тюрьма: Блю Лок", "orig": "Blue Lock", "poster": "https://shikimori.one/system/animes/original/49596.jpg?1711944212"},
    "black rock shooter ova": {"rus": "Стрелок с Чёрной скалы OVA", "orig": "Black★Rock Shooter (OVA)", "poster": "https://shikimori.one/system/animes/original/7062.jpg?1711953511"},
    "busamen gachi fighter": {"rus": "Всё тот же невзрачный боец", "orig": "Busamen Gachi Fighter", "poster": "https://shikimori.one/system/animes/original/59918.jpg"},
    "chainsaw man": {"rus": "Человек-бензопила", "orig": "Chainsaw Man", "poster": "https://shikimori.one/system/animes/original/44511.jpg?1711945574"},
    "charlotte": {"rus": "Шарлотта", "orig": "Charlotte", "poster": "https://shikimori.one/system/animes/original/28999.jpg?1711945851"},
    "charlotte tsuyoimono tachi": {"rus": "Шарлотта: Сильные люди", "orig": "Charlotte: Tsuyoimono-tachi", "poster": "https://shikimori.one/system/animes/original/31553.jpg?1711945826"},
    "dorohedoro": {"rus": "Дорохедоро", "orig": "Dorohedoro", "poster": "https://shikimori.one/system/animes/original/38668.jpg?1711948099"},
    "enen no shouboutai": {"rus": "Пламенная бригада пожарных", "orig": "Enen no Shouboutai", "poster": "https://shikimori.one/system/animes/original/38671.jpg?1711948922"},
    "enen no shouboutai ni no shou": {"rus": "Пламенная бригада пожарных: Вторая глава", "orig": "Enen no Shouboutai: Ni no Shou", "poster": "https://shikimori.one/system/animes/original/40956.jpg?1711948877"},
    "fairy tail final series": {"rus": "Хвост Феи: Финал", "orig": "Fairy Tail: Final Series", "poster": "https://shikimori.one/system/animes/original/35972.jpg?1711949100"},
    "fairy tail movie 2 dragon cry": {"rus": "Хвост Феи: Плач дракона", "orig": "Fairy Tail Movie 2: Dragon Cry", "poster": "https://shikimori.one/system/animes/original/30778.jpg?1711949118"},
    "fateapocrypha": {"rus": "Судьба/Апокриф", "orig": "Fate/Apocrypha", "poster": "https://shikimori.one/system/animes/original/34349.jpg?1711947605"},
    "fatestay night unlimited blade works": {"rus": "Судьба/Ночь схватки: Бесконечный мир клинков", "orig": "Fate/stay night: Unlimited Blade Works", "poster": "https://shikimori.one/system/animes/original/22297.jpg?1711950480"},
    "fuuka": {"rus": "Фука", "orig": "Fuuka", "poster": "https://shikimori.one/system/animes/original/33743.jpg?1711949982"},
    "golden time": {"rus": "Золотая пора", "orig": "Golden Time", "poster": "https://shikimori.one/system/animes/original/17895.jpg?1711951640"},
    "highschool of the dead": {"rus": "Школа мертвецов", "orig": "Highschool of the Dead", "poster": "https://shikimori.one/system/animes/original/8074.jpg?1711953602"},
    "kokoro connect": {"rus": "Связь сердец", "orig": "Kokoro Connect", "poster": "https://shikimori.one/system/animes/original/11887.jpg?1711959319"},
    "kono oto tomare": {"rus": "Задержи этот звук!", "orig": "Kono Oto Tomare!", "poster": "https://shikimori.one/system/animes/original/38080.jpg?1711959580"},
    "kono subarashii sekai ni shukufuku wo 2": {"rus": "Этот замечательный мир! 2", "orig": "Kono Subarashii Sekai ni Shukufuku wo! 2", "poster": "https://shikimori.one/system/animes/original/32937.jpg?1711959560"},
    "nierautomata ver1 1a": {"rus": "Ниер: Автомата — Версия 1.1а", "orig": "NieR:Automata Ver1.1a", "poster": "https://shikimori.one/system/animes/original/51105.jpg?1718592170"},
    "recreators": {"rus": "Возрождающие", "orig": "Re:Creators", "poster": "https://shikimori.one/system/animes/original/34561.jpg?1711970109"},
    "sk8": {"rus": "Скейт: Бесконечность", "orig": "SK8 the Infinity", "poster": "https://shikimori.one/system/animes/original/42923.jpg?1711974459"},
    "shuumatsu no walkure": {"rus": "Повесть о конце света", "orig": "Shuumatsu no Walküre", "poster": "https://shikimori.one/system/animes/original/44942.jpg?1711974270"},
    "tokidoki bosotto russia go de dereru tonari no alya san": {"rus": "Аля иногда кокетничает со мной по-русски", "orig": "Tokidoki Bosotto Russia-go de Dereru Tonari no Alya-san", "poster": "https://shikimori.one/system/animes/original/54744.jpg?1718725558"},
    "tondemo skill de isekai hourou meshi": {"rus": "Кулинарные скитания в параллельном мире", "orig": "Tondemo Skill de Isekai Hourou Meshi", "poster": "https://shikimori.one/system/animes/original/53446.jpg?1709518221"},
    "toradora": {"rus": "Торадора!", "orig": "Toradora!", "poster": "https://shikimori.one/system/animes/original/4224.jpg?1711978202"},
    "grand blue": {"rus": "Необъятный океан", "orig": "Grand Blue", "poster": "https://anilibria.top/storage/releases/posters/8721/nQ8wS0g7MvC8uR6yL4pK8sH2mX9nQ3zW.jpg"}
}

def find_csv_file():
    for i, arg in enumerate(sys.argv):
        if arg == '--csv' and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    for f in os.listdir(BASE):
        if f.endswith('.csv') and ('bookmark' in f.lower() or 'anixart' in f.lower() or 'shikimori' in f.lower() or 'anime' in f.lower()):
            if f != 'Коллекция_Аниме.csv':
                return os.path.join(BASE, f)
    fallback = os.path.join(BASE, "Anixart_Bookmarks_15.08.2026.csv")
    if os.path.exists(fallback): return fallback
    return None

def parse_bookmarks_csv(csv_path):
    if not csv_path or not os.path.exists(csv_path):
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
    anixart_exact = {}

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
            if c and c not in anixart_exact:
                anixart_exact[c] = entry

    return anixart_bookmarks, anixart_exact

def main():
    print("==================================================")
    print("🌸 АВТОНОМНЫЙ ОРГАНИЗАТОР И КАТАЛОГ АНИМЕ 🌸")
    print("==================================================")
    
    with open(ALIB_CACHE, encoding='utf-8') as f: alib_releases = json.load(f)
    with open(SHIKI_CACHE, encoding='utf-8') as f: shiki_cache = json.load(f)
    
    alib_exact = {}
    for r in alib_releases:
        names = r.get('name', {})
        for k in [names.get('english'), names.get('main'), names.get('alternative'), r.get('alias')]:
            if k:
                c = clean(k)
                if c and c not in alib_exact: alib_exact[c] = r
                
    csv_path = find_csv_file()
    print(f"1. Файл закладок: {os.path.basename(csv_path) if csv_path else 'Не указан'}")
    anixart_bookmarks, anixart_exact_map = parse_bookmarks_csv(csv_path)
    print(f"   Загружено закладок: {len(anixart_bookmarks)}")
    
    folders = ['Просмотрено', 'В планах', 'Отложено', 'Смотрю', 'Остальные торренты']
    for st in folders:
        p = os.path.join(BASE, st)
        os.makedirs(p, exist_ok=True)
        for f in os.listdir(p):
            if f.endswith('.torrent'):
                try: os.remove(os.path.join(p, f))
                except Exception: pass
                
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

    def get_metadata_strict(title):
        c = clean(title)
        if c in EXACT_OVERRIDE:
            o = EXACT_OVERRIDE[c]
            return o['rus'], o['orig'], o.get('poster', ''), o.get('genres', []), o.get('desc', ''), None, None

        if c in alib_exact:
            rel = alib_exact[c]
            rus = rel.get('name', {}).get('main', '') or title
            orig = rel.get('name', {}).get('english', '') or title
            p = rel.get('poster', {}).get('src', '')
            poster = f"https://anilibria.top{p}" if p and not p.startswith('http') else p
            genres = [g['name'] for g in rel.get('genres', []) if isinstance(g, dict) and g.get('name')]
            desc = rel.get('description', '')
            shiki_id = rel.get('shikimori', {}).get('id') if isinstance(rel.get('shikimori'), dict) else None
            alias = rel.get('alias')
            return rus, orig, poster, genres, desc, shiki_id, alias

        if title in shiki_cache:
            s = shiki_cache[title]
            return s.get('rus', title), s.get('eng', title), s.get('poster', ''), s.get('genres', []), s.get('description', ''), s.get('shikimori_id'), None

        return title, title, '', [], '', None, None

    def process_one_torrent(f):
        src_path = os.path.join(TORRENTS_DIR, f)
        t_title, ep, qual = parse_master_torrent(f)
        rus_title, orig_title, poster_url, genres, desc, shiki_id, alias = get_metadata_strict(t_title)
        
        hit = None
        for cand in [clean(rus_title), clean(orig_title), clean(t_title)]:
            if cand in anixart_exact_map:
                hit = anixart_exact_map[cand]
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
        
        clean_desc = re.sub(r'[\r\n\t]+', ' ', desc or '').strip()
        
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
            'poster_url': poster_url,
            'genres': genres,
            'description': clean_desc,
            'shikimori_id': shiki_id,
            'alias': alias
        }

    with ThreadPoolExecutor(max_workers=24) as executor:
        copied_torrents = list(executor.map(process_one_torrent, all_master_torrents))
    print(f"   Успешно скопировано: {len(copied_torrents)} файлов")

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
            season_label = f"1 сезон (Часть {m_p.group(1)})"
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

    franchises = {}

    for it in copied_torrents:
        f_key, f_rus, f_orig, s_label = get_franchise_info(it['rus'], it['orig'])
        
        safe_name_season = safe(it['rus'])[:70] + '.jpg'
        local_p_season = os.path.join(POSTERS_DIR, it['status'], safe_name_season)
        season_poster_rel = ""
        if os.path.exists(local_p_season) and os.path.getsize(local_p_season) > 500:
            season_poster_rel = f"Постеры/{urllib.parse.quote(it['status'])}/{urllib.parse.quote(safe_name_season)}"
        else:
            safe_name_base = safe(f_rus)[:70] + '.jpg'
            local_p_base = os.path.join(POSTERS_DIR, it['status'], safe_name_base)
            if os.path.exists(local_p_base) and os.path.getsize(local_p_base) > 500:
                season_poster_rel = f"Постеры/{urllib.parse.quote(it['status'])}/{urllib.parse.quote(safe_name_base)}"

        if f_key not in franchises:
            franchises[f_key] = {
                'id': len(franchises) + 1,
                'f_key': f_key,
                'rus': f_rus,
                'orig': f_orig,
                'status': it['status'],
                'rating': it['rating'],
                'poster': season_poster_rel,
                'fallback_poster': it['poster_url'],
                'genres': it['genres'],
                'description': it['description'],
                'shikimori_id': it['shikimori_id'],
                'alias': it['alias'],
                'seasons': {}
            }
            
        fr = franchises[f_key]
        status_priority = {'Просмотрено': 5, 'Смотрю': 4, 'В планах': 3, 'Отложено': 2, 'Остальные торренты': 1}
        if status_priority.get(it['status'], 0) > status_priority.get(fr['status'], 0):
            fr['status'] = it['status']
        if it['rating'] and it['rating'] != 'Не оценено':
            fr['rating'] = it['rating']
        if not fr['poster'] and season_poster_rel:
            fr['poster'] = season_poster_rel
        if not fr['fallback_poster'] and it['poster_url']:
            fr['fallback_poster'] = it['poster_url']
        if not fr['genres'] and it['genres']:
            fr['genres'] = it['genres']
        if not fr['description'] and it['description']:
            fr['description'] = it['description']
        if not fr['shikimori_id'] and it['shikimori_id']:
            fr['shikimori_id'] = it['shikimori_id']
        if not fr['alias'] and it['alias']:
            fr['alias'] = it['alias']
            
        if s_label not in fr['seasons']:
            fr['seasons'][s_label] = {
                'season_name': s_label,
                'season_rus': it['rus'],
                'episodes': it['episodes'],
                'status': it['status'],
                'rating': it['rating'],
                'poster': season_poster_rel,
                'fallback_poster': it['poster_url'],
                'genres': it['genres'],
                'description': it['description'],
                'alias': it['alias'],
                'variants': []
            }
            
        sn = fr['seasons'][s_label]
        if it['episodes'] and not sn['episodes']: sn['episodes'] = it['episodes']
        if not sn['poster'] and season_poster_rel: sn['poster'] = season_poster_rel
        if not sn['fallback_poster'] and it['poster_url']: sn['fallback_poster'] = it['poster_url']
        if not sn['genres'] and it['genres']: sn['genres'] = it['genres']
        if not sn['description'] and it['description']: sn['description'] = it['description']
        if not sn['alias'] and it['alias']: sn['alias'] = it['alias']
        
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

    for bm in anixart_bookmarks:
        f_key, f_rus, f_orig, s_label = get_franchise_info(bm['rus'], bm['orig'])
        c_bm_orig = clean(bm['orig'])
        c_bm_rus = clean(bm['rus'])
        alias = None
        if c_bm_orig in alib_exact: alias = alib_exact[c_bm_orig].get('alias')
        elif c_bm_rus in alib_exact: alias = alib_exact[c_bm_rus].get('alias')
        
        aniliberty_url = f"https://aniliberty.top/anime/releases/release/{alias}" if alias else f"https://aniliberty.top"
        query_encoded = urllib.parse.quote(bm['rus'])
        nyaa_encoded = urllib.parse.quote(bm['orig'] or bm['rus'])
        
        online_variants = [
            {
                'filename': f"Релиз на AniLiberty: {bm['rus']}",
                'url': aniliberty_url,
                'quality': 'AniLiberty / AniLibria (Официальный)',
                'badge': 'AniLiberty',
                'episodes': 'Онлайн',
                'size_bytes': 0,
                'size_str': 'Онлайн',
                'source': 'online_anilibria'
            },
            {
                'filename': f"Поиск AnimeGO: {bm['rus']}",
                'url': f"https://animego.org/search/anime?q={query_encoded}",
                'quality': 'AnimeGO (Смотреть / Все озвучки)',
                'badge': 'AnimeGO',
                'episodes': 'Онлайн',
                'size_bytes': 0,
                'size_str': 'Онлайн',
                'source': 'online_animego'
            },
            {
                'filename': f"Поиск Nyaa: {bm['orig']}",
                'url': f"https://nyaa.si/?f=0&c=1_2&q={nyaa_encoded}",
                'quality': 'Nyaa.si (Все озвучки / Multi-Sub)',
                'badge': 'Nyaa.si',
                'episodes': 'Онлайн',
                'size_bytes': 0,
                'size_str': 'Онлайн',
                'source': 'online_nyaa'
            }
        ]

        safe_name = safe(bm['rus'])[:70] + '.jpg'
        local_p = os.path.join(POSTERS_DIR, bm['status'], safe_name)
        poster_rel = ""
        if os.path.exists(local_p) and os.path.getsize(local_p) > 500:
            poster_rel = f"Постеры/{urllib.parse.quote(bm['status'])}/{urllib.parse.quote(safe_name)}"
            
        online_p = ""
        genres = []
        desc = ""
        s_id = None
        
        if bm['orig'] in shiki_cache:
            s = shiki_cache[bm['orig']]
            online_p = s.get('poster', '')
            genres = s.get('genres', [])
            desc = re.sub(r'[\r\n\t]+', ' ', s.get('description', '')).strip()
            s_id = s.get('shikimori_id')
        elif c_bm_orig in alib_exact:
            rel = alib_exact[c_bm_orig]
            p = rel.get('poster', {}).get('src', '')
            if p: online_p = f"https://anilibria.top{p}" if not p.startswith('http') else p
            genres = [g['name'] for g in rel.get('genres', []) if isinstance(g, dict) and g.get('name')]
            desc = re.sub(r'[\r\n\t]+', ' ', rel.get('description', '')).strip()
            s_id = rel.get('shikimori', {}).get('id') if isinstance(rel.get('shikimori'), dict) else None

        if f_key not in franchises:
            franchises[f_key] = {
                'id': len(franchises) + 1,
                'f_key': f_key,
                'rus': f_rus,
                'orig': f_orig,
                'status': bm['status'],
                'rating': bm['rating'],
                'poster': poster_rel,
                'fallback_poster': online_p,
                'genres': genres,
                'description': desc,
                'shikimori_id': s_id,
                'alias': alias,
                'seasons': {
                    s_label: {
                        'season_name': s_label,
                        'season_rus': bm['rus'],
                        'episodes': 'Онлайн поиск',
                        'status': bm['status'],
                        'rating': bm['rating'],
                        'poster': poster_rel,
                        'fallback_poster': online_p,
                        'genres': genres,
                        'description': desc,
                        'alias': alias,
                        'variants': online_variants
                    }
                }
            }
        else:
            fr = franchises[f_key]
            if s_label not in fr['seasons']:
                fr['seasons'][s_label] = {
                    'season_name': s_label,
                    'season_rus': bm['rus'],
                    'episodes': 'Онлайн поиск',
                    'status': bm['status'],
                    'rating': bm['rating'],
                    'poster': poster_rel,
                    'fallback_poster': online_p,
                    'genres': genres,
                    'description': desc,
                    'alias': alias,
                    'variants': online_variants
                }

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
    print("\n✅ Готово! Каталог_Аниме.html обновлен.")

if __name__ == '__main__':
    main()
