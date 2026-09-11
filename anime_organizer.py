# anime_organizer.py — единый финальный скрипт
# Запуск: python anime_organizer.py
import os, sys, re, csv, io, json, shutil, urllib.request, urllib.parse, ssl, time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout.reconfigure(encoding='utf-8')

BASE         = os.path.dirname(os.path.abspath(__file__))
TORRENTS_DIR = os.path.join(BASE, "торренты")
POSTERS_DIR  = os.path.join(BASE, "Постеры")
CSV_FILE     = os.path.join(BASE, "Anixart_Bookmarks_15.08.2026.csv")
ALIB_CACHE   = os.path.join(BASE, "anilibria_cache.json")
SHIKI_CACHE  = os.path.join(BASE, "shikimori_cache.json")
ALIB_BASE    = "https://anilibria.top"

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode    = ssl.CERT_NONE

MANUAL = {
    "bluelock":                 ("Синяя тюрьма: Блю Лок",              "BLUELOCK"),
    "fateapocrypha":            ("Судьба/Апокриф",                      "Fate/Apocrypha"),
    "recreators":               ("Возрождающие",                        "Re:Creators"),
    "sk8":                      ("Скейт: Бесконечность",                "SK8 the Infinity"),
    "shuumatsu no walkure":     ("Повесть о конце света",               "Shuumatsu no Walküre"),
    "black rock shooter (ova)": ("Стрелок с Чёрной скалы OVA",          "Black Rock Shooter OVA"),
}

# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────
def ck(s):
    if not s: return ""
    s = re.sub(r'[^\w\s]', ' ', s.lower())
    return re.sub(r'\s+', ' ', s).strip()

def safe(name):
    name = name.replace('/', '／').replace('\\', '／')
    for c in ':<>"|?*': name = name.replace(c, '')
    return re.sub(r'\s+', ' ', name).strip()

# ─────────────────────────────────────────────
#  1. Parse CSV
# ─────────────────────────────────────────────
def parse_csv():
    with open(CSV_FILE, encoding='utf-8-sig') as f:
        raw = f.read()
    rows, cur = [], ''
    for line in raw.split('\n'):
        ls = line.strip()
        if not ls: continue
        if re.match(r'^\d+,', ls):
            if cur: rows.append(cur)
            cur = ls
        else:
            cur += ' ' + ls
    if cur: rows.append(cur)

    entries, lookup = [], {}
    for r in rows:
        m = re.match(r'^(\d+),(.*)$', r)
        if not m: continue
        rest = m.group(2)
        tail = re.search(r',(Добавлено|Не добавлено),([^,]+),([^,]*)$', rest)
        if not tail: continue
        status, rating = tail.group(2).strip(), tail.group(3).strip()
        titles_csv = rest[:tail.start()]
        pts = next(csv.reader(io.StringIO(titles_csv)), [])
        rus  = pts[0].strip() if pts     else titles_csv
        orig = pts[1].strip() if len(pts)>1 else ''
        alts = pts[2].strip() if len(pts)>2 else ''
        e = dict(rus=rus, orig=orig, alts=alts, status=status, rating=rating)
        entries.append(e)
        for t in [rus, orig] + [a.strip() for a in alts.split(',')]:
            k = ck(t)
            if k and k not in lookup: lookup[k] = e
    return entries, lookup

# ─────────────────────────────────────────────
#  2. Build indices
# ─────────────────────────────────────────────
def build_indices():
    with open(ALIB_CACHE,  encoding='utf-8') as f: alib  = json.load(f)
    with open(SHIKI_CACHE, encoding='utf-8') as f: shiki = json.load(f)

    alib_idx = {}
    for r in alib:
        n = r.get('name', {})
        for v in [n.get('english'), n.get('main'), n.get('alternative'), r.get('alias')]:
            k = ck(v or '')
            if k and k not in alib_idx: alib_idx[k] = r
    return alib_idx, shiki

# ─────────────────────────────────────────────
#  3. Resolve a torrent title -> (rus, orig, poster_url)
# ─────────────────────────────────────────────
def resolve(title, alib_idx, shiki):
    k = ck(title)

    # manual override
    if k in MANUAL:
        rus, orig = MANUAL[k]
        # poster from alib
        for v in [orig, rus]:
            rel = alib_idx.get(ck(v))
            if rel:
                p = rel.get('poster', {}).get('src','')
                if p: return rus, orig, (ALIB_BASE+p if not p.startswith('http') else p)
        return rus, orig, ''

    # exact AniLibria
    rel = alib_idx.get(k)
    if rel:
        n = rel.get('name', {})
        rus  = n.get('main','') or title
        orig = n.get('english','') or title
        p    = rel.get('poster', {}).get('src','')
        poster = (ALIB_BASE+p) if p and not p.startswith('http') else p
        return rus, orig, poster

    # shikimori cache
    if title in shiki:
        s = shiki[title]
        return s.get('rus', title), s.get('eng', title), s.get('poster','')

    # fuzzy AniLibria (substring)
    for ak, rel in alib_idx.items():
        if len(ak) > 4 and len(k) > 4 and (ak in k or k in ak):
            n = rel.get('name', {})
            rus  = n.get('main','') or title
            orig = n.get('english','') or title
            p    = rel.get('poster', {}).get('src','')
            poster = ALIB_BASE+p if p and not p.startswith('http') else p
            return rus, orig, poster

    return title, title, ''

# ─────────────────────────────────────────────
#  4. Match torrent -> Anixart status
# ─────────────────────────────────────────────
def match_anixart(rus, orig, torrent_key, lookup, entries):
    for cand in [ck(rus), ck(orig), torrent_key]:
        if cand in lookup: return lookup[cand]
    # substring
    cru, cor = ck(rus), ck(orig)
    for e in entries:
        er, eo = ck(e['rus']), ck(e['orig'])
        if (len(er)>5 and (er in cru or cru in er)) or (len(eo)>5 and (eo in cor or cor in eo)):
            return e
    return None

# ─────────────────────────────────────────────
#  5. Parse torrent filename
# ─────────────────────────────────────────────
def parse_torrent(fname):
    base = fname[:-8]  # strip .torrent
    tags = re.findall(r'\[([^\]]+)\]', base)
    title = re.sub(r'\s*-\s*AniLibria\.TV.*$', '', base, flags=re.I)
    title = re.sub(r'\s*\[.*$', '', title).strip()

    ep_parts, q_parts = [], []
    for t in tags:
        lo = t.lower()
        if re.search(r'^\d', t) or any(w in lo for w in ('movie','film','фильм','ova','ona','sp','recap','рекап')):
            ep_parts.append(t)
        else:
            q_parts.append(t)

    ep_raw = ' '.join(ep_parts)
    # nicely format episodes
    if re.match(r'^\d+-\d+$', ep_raw) or re.match(r'^\d+$', ep_raw):
        ep = f"{ep_raw} серий"
    elif ep_raw:
        ep_lo = ep_raw.lower()
        if 'movie' in ep_lo or 'фильм' in ep_lo or 'film' in ep_lo:
            ep = 'Фильм'
        else:
            ep = ep_raw
    else:
        ep = ''

    qual = ' '.join(q_parts)
    return title, ep, qual

# ─────────────────────────────────────────────
#  6. Download poster
# ─────────────────────────────────────────────
def dl_poster(task):
    url, path = task
    if not url: return False
    if os.path.exists(path) and os.path.getsize(path) > 500: return True
    os.makedirs(os.path.dirname(path), exist_ok=True)
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Referer': 'https://anilibria.top/'
    })
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=10, context=ctx) as r, open(path,'wb') as f:
                f.write(r.read())
            return True
        except Exception:
            time.sleep(0.4)
    return False

# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def main():
    print("=== АНИМЕ-ОРГАНИЗАТОР ===")

    print("[1/5] Читаем CSV Anixart...")
    entries, lookup = parse_csv()
    print(f"      Записей: {len(entries)}")

    print("[2/5] Строим индексы AniLibria+Shikimori...")
    alib_idx, shiki = build_indices()
    print(f"      AniLibria релизов: {len(alib_idx)}")

    print("[3/5] Обрабатываем торренты...")
    torrent_files = [f for f in os.listdir(TORRENTS_DIR) if f.endswith('.torrent')]

    STATUS_FOLDERS = {
        "Просмотрено":       os.path.join(BASE, "Просмотрено"),
        "В планах":          os.path.join(BASE, "В планах"),
        "Отложено":          os.path.join(BASE, "Отложено"),
        "Смотрю":            os.path.join(BASE, "Смотрю"),
        "Остальные торренты": os.path.join(BASE, "Остальные торренты"),
    }
    for d in STATUS_FOLDERS.values():
        os.makedirs(d, exist_ok=True)

    processed  = []
    dl_tasks   = {}   # poster_dest -> url  (deduplicated)
    stat_count = {k: 0 for k in STATUS_FOLDERS}

    for fname in torrent_files:
        src   = os.path.join(TORRENTS_DIR, fname)
        title, ep, qual = parse_torrent(fname)
        tkey  = ck(title)

        rus, orig, poster_url = resolve(title, alib_idx, shiki)
        hit   = match_anixart(rus, orig, tkey, lookup, entries)

        if hit:
            status = hit['status']
            rating = hit['rating']
            rus    = hit['rus']  # prefer user's preferred name
        else:
            status = "Остальные торренты"
            rating = "Не оценено"

        # new filename
        parts = []
        if ep:   parts.append(ep)
        if qual: parts.append(f"[{qual}]")
        suffix = ' '.join(parts)
        new_name = f"{safe(rus)} ／ {safe(orig)} - {suffix}.torrent" if suffix else f"{safe(rus)} ／ {safe(orig)}.torrent"
        dest_dir  = STATUS_FOLDERS.get(status, STATUS_FOLDERS["Остальные торренты"])
        dest_path = os.path.join(dest_dir, new_name)

        # poster path
        poster_safe = safe(rus)[:80] + ".jpg"
        poster_dest = os.path.join(POSTERS_DIR, status, poster_safe)
        if poster_url and poster_dest not in dl_tasks:
            dl_tasks[poster_dest] = poster_url

        # relative poster path for HTML
        try:
            poster_rel = os.path.relpath(poster_dest, BASE).replace('\\','/')
        except ValueError:
            poster_rel = poster_url

        stat_count[status] = stat_count.get(status, 0) + 1
        processed.append(dict(
            fname=fname, src=src, dest=dest_path,
            new_name=new_name,
            rus=rus, orig=orig,
            ep=ep, qual=qual,
            status=status, rating=rating,
            poster_url=poster_url,
            poster_dest=poster_dest,
            poster_rel=poster_rel,
        ))

    print(f"      Обработано: {len(processed)} торрентов")
    for st, cnt in stat_count.items():
        print(f"        {st}: {cnt}")

    # ── Copy torrents ──
    print("[3/5] Копируем торренты в папки...")
    ok_copy = 0
    for it in processed:
        try:
            shutil.copy2(it['src'], it['dest'])
            ok_copy += 1
        except Exception as e:
            # duplicate new_name in same folder → append original name fragment
            base_name = it['new_name'][:-8]
            alt = os.path.join(os.path.dirname(it['dest']),
                               f"{base_name} [{it['fname'][:30]}].torrent")
            try:
                shutil.copy2(it['src'], alt)
                it['dest'] = alt
                it['new_name'] = os.path.basename(alt)
                ok_copy += 1
            except Exception:
                pass
    print(f"      Скопировано: {ok_copy}/{len(processed)}")

    # ── Download posters ──
    print("[4/5] Скачиваем постеры (параллельно)...")
    os.makedirs(POSTERS_DIR, exist_ok=True)
    for st in STATUS_FOLDERS:
        os.makedirs(os.path.join(POSTERS_DIR, st), exist_ok=True)

    tasks_list = list(dl_tasks.items())   # (dest, url)
    dl_ok = 0
    with ThreadPoolExecutor(max_workers=24) as ex:
        futs = {ex.submit(dl_poster, (url, dest)): dest for dest, url in tasks_list}
        for i, fut in enumerate(as_completed(futs)):
            if fut.result(): dl_ok += 1
            if (i+1) % 200 == 0 or (i+1) == len(futs):
                print(f"      Постеры: {i+1}/{len(futs)} ({dl_ok} ок)")
    print(f"      Загружено: {dl_ok}/{len(tasks_list)}")

    # ── Reports ──
    print("[5/5] Генерируем отчёты...")
    gen_txt(processed)
    gen_csv_report(processed)
    gen_html(processed, stat_count)
    print("      Готово!")

    print("\n=== РЕЗУЛЬТАТ ===")
    for st, cnt in stat_count.items():
        print(f"  {st}: {cnt} файлов")
    print(f"\n  Постеры   -> Постеры/")
    print(f"  Каталог   -> Каталог_Аниме.html")
    print(f"  Отчёт     -> Результаты_сортировки.txt")
    print(f"  Таблица   -> Коллекция_Аниме.csv")

# ─────────────────────────────────────────────
#  Reports
# ─────────────────────────────────────────────
def gen_txt(items):
    path = os.path.join(BASE, "Результаты_сортировки.txt")
    by_st = {}
    for it in items:
        by_st.setdefault(it['status'], []).append(it)
    order = ["Просмотрено","В планах","Смотрю","Отложено","Остальные торренты"]
    with open(path, 'w', encoding='utf-8') as f:
        f.write("=" * 100 + "\n")
        f.write("ОТЧЁТ ПО СОРТИРОВКЕ И ПЕРЕИМЕНОВАНИЮ АНИМЕ-ТОРРЕНТОВ\n")
        f.write("=" * 100 + "\n\n")
        f.write(f"Всего обработано: {len(items)}\n")
        for st in order:
            c = len(by_st.get(st, []))
            f.write(f"  {st}: {c}\n")
        f.write("\n")
        for st in order:
            grp = by_st.get(st, [])
            if not grp: continue
            f.write(f"\n{'='*100}\n[{st.upper()}]  ({len(grp)} файлов)\n{'='*100}\n")
            for it in sorted(grp, key=lambda x: x['rus'].lower()):
                f.write(f"\n  {it['rus']}  /  {it['orig']}\n")
                f.write(f"    Серии: {it['ep'] or '—'}  |  Качество: {it['qual'] or '—'}  |  Оценка: {it['rating']}\n")
                f.write(f"    Было:  {it['fname']}\n")
                f.write(f"    Стало: {it['new_name']}\n")


def gen_csv_report(items):
    path = os.path.join(BASE, "Коллекция_Аниме.csv")
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(["Статус","Оценка","Русское название","Оригинальное название",
                    "Серии","Качество","Новый файл","Исходный файл","Постер URL"])
        for it in sorted(items, key=lambda x: (x['status'], x['rus'].lower())):
            w.writerow([it['status'], it['rating'], it['rus'], it['orig'],
                        it['ep'], it['qual'], it['new_name'], it['fname'], it['poster_url']])


def gen_html(items, stat_count):
    path = os.path.join(BASE, "Каталог_Аниме.html")

    total    = len(items)
    viewed   = stat_count.get("Просмотрено", 0)
    plans    = stat_count.get("В планах", 0)
    paused   = stat_count.get("Отложено", 0)
    watching = stat_count.get("Смотрю", 0)
    others   = stat_count.get("Остальные торренты", 0)

    # Build JS data  (only user catalogue: Просмотрено + В планах + Отложено + Смотрю)
    js_items = []
    for idx, it in enumerate(items):
        # poster: prefer local file
        if os.path.exists(it['poster_dest']) and os.path.getsize(it['poster_dest']) > 500:
            poster_src = it['poster_rel']
        else:
            poster_src = it['poster_url'] or ''

        js_items.append({
            'id':      idx,
            'rus':     it['rus'],
            'orig':    it['orig'],
            'status':  it['status'],
            'rating':  it['rating'],
            'ep':      it['ep'],
            'qual':    it['qual'],
            'poster':  poster_src,
            'fbposter': it['poster_url'] or '',
            'file':    it['new_name'],
            'folder':  urllib.parse.quote(it['status']) + '/' + urllib.parse.quote(it['new_name']),
        })

    jdata = json.dumps(js_items, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Моя коллекция аниме</title>
<style>
:root{{
  --bg:#0b0e14;--bg2:#121824;--card:#182234;--card2:#1f2d45;
  --pink:#ff2a5f;--purple:#7928ca;--cyan:#00dfd8;--green:#10b981;--gold:#f59e0b;
  --t1:#f8fafc;--t2:#94a3b8;--t3:#64748b;
  --border:rgba(255,255,255,.08);
}}
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--t1);
  background-image:radial-gradient(circle at 10% 10%,rgba(121,40,202,.15) 0%,transparent 40%),
                   radial-gradient(circle at 90% 90%,rgba(255,42,95,.12) 0%,transparent 40%);
  background-attachment:fixed;padding-bottom:60px;}}
/* ─ Header ─ */
header{{position:sticky;top:0;z-index:99;background:rgba(18,24,36,.88);
  backdrop-filter:blur(16px);border-bottom:1px solid var(--border);padding:14px 24px;}}
.hrow{{max-width:1600px;margin:0 auto;display:flex;align-items:center;gap:16px;flex-wrap:wrap;}}
.logo{{display:flex;align-items:center;gap:10px;}}
.logo-ico{{font-size:30px;animation:pulse 2.5s infinite;}}
@keyframes pulse{{0%,100%{{transform:scale(1)}}50%{{transform:scale(1.1)}}}}
.logo h1{{font-size:20px;font-weight:800;background:linear-gradient(135deg,#fff,#cbd5e1);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;}}
.logo span{{font-size:11px;color:var(--pink);font-weight:700;letter-spacing:.5px;}}
.search-wrap{{flex:1;max-width:480px;position:relative;}}
.search-wrap input{{width:100%;background:rgba(255,255,255,.05);border:1px solid var(--border);
  border-radius:10px;padding:11px 14px 11px 40px;color:#fff;font-size:14px;outline:none;
  transition:.25s;}}
.search-wrap input:focus{{border-color:var(--pink);background:rgba(255,255,255,.08);
  box-shadow:0 0 0 3px rgba(255,42,95,.18);}}
.si{{position:absolute;left:13px;top:50%;transform:translateY(-50%);color:var(--t3);}}
/* ─ Stats ─ */
.stats{{max-width:1600px;margin:22px auto 0;padding:0 24px;
  display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;}}
.sc{{background:var(--bg2);border:1px solid var(--border);border-radius:12px;
  padding:14px 16px;display:flex;align-items:center;gap:14px;transition:.2s;}}
.sc:hover{{transform:translateY(-3px);border-color:rgba(255,255,255,.18);}}
.si-box{{width:44px;height:44px;border-radius:9px;display:flex;align-items:center;
  justify-content:center;font-size:22px;}}
.pink-b{{background:rgba(255,42,95,.15);color:var(--pink);}}
.green-b{{background:rgba(16,185,129,.15);color:var(--green);}}
.cyan-b{{background:rgba(0,223,216,.15);color:var(--cyan);}}
.gold-b{{background:rgba(245,158,11,.15);color:var(--gold);}}
.purple-b{{background:rgba(121,40,202,.15);color:var(--purple);}}
.snum{{font-size:22px;font-weight:800;}}
.slbl{{font-size:11px;color:var(--t3);text-transform:uppercase;letter-spacing:.5px;font-weight:600;}}
/* ─ Controls ─ */
.ctrl{{max-width:1600px;margin:20px auto;padding:0 24px;
  display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:14px;}}
.tabs{{display:flex;background:var(--bg2);padding:5px;border-radius:12px;
  border:1px solid var(--border);gap:5px;flex-wrap:wrap;}}
.tab{{background:transparent;border:none;color:var(--t2);font:600 13px/1 'Segoe UI',system-ui;
  padding:8px 16px;border-radius:8px;cursor:pointer;display:flex;align-items:center;gap:6px;transition:.2s;}}
.tab:hover{{color:#fff;background:rgba(255,255,255,.05);}}
.tab.active{{background:var(--pink);color:#fff;box-shadow:0 4px 14px rgba(255,42,95,.35);}}
.tc{{background:rgba(0,0,0,.25);padding:2px 6px;border-radius:20px;font-size:10px;}}
select.sort{{background:var(--bg2);border:1px solid var(--border);border-radius:10px;
  color:#fff;padding:9px 14px;font:600 13px 'Segoe UI',system-ui;outline:none;cursor:pointer;}}
select.sort:focus{{border-color:var(--pink);}}
/* ─ Grid ─ */
.grid{{max-width:1600px;margin:0 auto;padding:0 24px;
  display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:20px;}}
/* ─ Card ─ */
.card{{background:var(--card);border:1px solid var(--border);border-radius:14px;
  overflow:hidden;display:flex;flex-direction:column;
  transition:transform .3s,border-color .3s,box-shadow .3s;
  box-shadow:0 8px 24px -8px rgba(0,0,0,.45);}}
.card:hover{{transform:translateY(-8px);border-color:rgba(255,42,95,.4);
  box-shadow:0 18px 36px -8px rgba(255,42,95,.22);background:var(--card2);}}
.poster{{position:relative;width:100%;padding-top:142%;background:#090c10;overflow:hidden;}}
.poster img{{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;transition:transform .4s;}}
.card:hover .poster img{{transform:scale(1.06);}}
.overlay{{position:absolute;inset:0;
  background:linear-gradient(to top,rgba(11,14,20,.97) 0%,rgba(11,14,20,.15) 55%,transparent);
  display:flex;flex-direction:column;justify-content:space-between;padding:10px;}}
.badges{{display:flex;justify-content:space-between;align-items:flex-start;gap:6px;}}
.sbadge{{font-size:10px;font-weight:700;padding:3px 8px;border-radius:20px;
  text-transform:uppercase;letter-spacing:.4px;backdrop-filter:blur(6px);}}
.s-viewed{{background:rgba(16,185,129,.9);color:#fff;}}
.s-plans{{background:rgba(0,223,216,.9);color:#0b0e14;}}
.s-paused{{background:rgba(245,158,11,.9);color:#0b0e14;}}
.s-watching{{background:rgba(121,40,202,.9);color:#fff;}}
.s-others{{background:rgba(100,116,139,.8);color:#fff;}}
.rbadge{{background:rgba(0,0,0,.75);border:1px solid rgba(245,158,11,.4);color:#fbbf24;
  font-size:11px;font-weight:700;padding:3px 8px;border-radius:7px;
  display:flex;align-items:center;gap:3px;backdrop-filter:blur(6px);}}
.cbody{{padding:14px;display:flex;flex-direction:column;flex:1;gap:10px;}}
.rus-title{{font-size:14px;font-weight:700;line-height:1.35;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;min-height:38px;}}
.orig-title{{font-size:11px;color:var(--t3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
.pills{{display:flex;gap:5px;flex-wrap:wrap;}}
.pill{{font-size:10px;font-weight:600;padding:2px 7px;border-radius:6px;}}
.p-ep{{background:rgba(0,223,216,.1);color:var(--cyan);border:1px solid rgba(0,223,216,.2);}}
.p-q{{background:rgba(255,42,95,.1);color:var(--pink);border:1px solid rgba(255,42,95,.2);}}
.dl-btn{{display:flex;align-items:center;justify-content:center;gap:6px;
  width:100%;padding:9px;border-radius:8px;border:1px solid rgba(255,42,95,.4);
  background:linear-gradient(135deg,rgba(255,42,95,.18),rgba(121,40,202,.18));
  color:#fff;font:700 12px 'Segoe UI',system-ui;text-decoration:none;transition:.2s;}}
.dl-btn:hover{{background:linear-gradient(135deg,var(--pink),var(--purple));
  box-shadow:0 4px 14px rgba(255,42,95,.4);transform:scale(1.02);}}
/* ─ Empty ─ */
.empty{{grid-column:1/-1;text-align:center;padding:80px 20px;color:var(--t3);}}
.empty .eico{{font-size:48px;margin-bottom:12px;}}
/* ─ Responsive ─ */
@media(max-width:680px){{
  .grid{{grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;padding:0 12px;}}
  .cbody{{padding:9px;}}
  .rus-title{{font-size:12px;}}
}}
</style>
</head>
<body>
<header>
  <div class="hrow">
    <div class="logo">
      <div class="logo-ico">🌸</div>
      <div>
        <h1>КОЛЛЕКЦИЯ АНИМЕ</h1>
        <span>Торренты • Постеры • Русские названия</span>
      </div>
    </div>
    <div class="search-wrap">
      <span class="si">🔍</span>
      <input type="text" id="q" placeholder="Поиск по названию, качеству, сериям…" oninput="render()">
    </div>
  </div>
</header>

<div class="stats">
  <div class="sc"><div class="si-box pink-b">📁</div><div><div class="snum">{total}</div><div class="slbl">Всего</div></div></div>
  <div class="sc"><div class="si-box green-b">⭐</div><div><div class="snum">{viewed}</div><div class="slbl">Просмотрено</div></div></div>
  <div class="sc"><div class="si-box cyan-b">📌</div><div><div class="snum">{plans}</div><div class="slbl">В планах</div></div></div>
  <div class="sc"><div class="si-box gold-b">⏸</div><div><div class="snum">{paused}</div><div class="slbl">Отложено</div></div></div>
  <div class="sc"><div class="si-box purple-b">▶</div><div><div class="snum">{watching}</div><div class="slbl">Смотрю</div></div></div>
</div>

<div class="ctrl">
  <div class="tabs">
    <button class="tab active" data-f="all">Все <span class="tc">{total}</span></button>
    <button class="tab" data-f="Просмотрено">⭐ Просмотрено <span class="tc">{viewed}</span></button>
    <button class="tab" data-f="В планах">📌 В планах <span class="tc">{plans}</span></button>
    <button class="tab" data-f="Отложено">⏸ Отложено <span class="tc">{paused}</span></button>
    <button class="tab" data-f="Смотрю">▶ Смотрю <span class="tc">{watching}</span></button>
    <button class="tab" data-f="Остальные торренты">📁 Остальные <span class="tc">{others}</span></button>
  </div>
  <select class="sort" id="srt" onchange="render()">
    <option value="default">По порядку</option>
    <option value="rating">По оценке ↓</option>
    <option value="az_ru">А–Я (рус.)</option>
    <option value="az_en">A–Z (orig.)</option>
  </select>
</div>

<main class="grid" id="grid"></main>

<script>
const DATA={jdata};
const PLACEHOLDER="https://images.unsplash.com/photo-1578632767115-351597cf2477?w=400&q=80";
let curFilter="all";

document.querySelectorAll(".tab").forEach(b=>b.addEventListener("click",()=>{{
  document.querySelectorAll(".tab").forEach(x=>x.classList.remove("active"));
  b.classList.add("active");
  curFilter=b.dataset.f;
  render();
}}));

function scls(st){{
  if(st==="Просмотрено")return"s-viewed";
  if(st==="В планах")return"s-plans";
  if(st==="Отложено")return"s-paused";
  if(st==="Смотрю")return"s-watching";
  return"s-others";
}}

function parseRating(r){{
  if(!r||r==="Не оценено")return 0;
  const m=r.match(/(\\d+)/);return m?+m[1]:0;
}}

function render(){{
  const q=document.getElementById("q").value.trim().toLowerCase();
  const srt=document.getElementById("srt").value;
  let list=DATA.filter(it=>{{
    if(curFilter!=="all"&&it.status!==curFilter)return false;
    if(!q)return true;
    return it.rus.toLowerCase().includes(q)||it.orig.toLowerCase().includes(q)||
           (it.qual||"").toLowerCase().includes(q)||(it.ep||"").toLowerCase().includes(q);
  }});
  if(srt==="rating")list.sort((a,b)=>parseRating(b.rating)-parseRating(a.rating));
  else if(srt==="az_ru")list.sort((a,b)=>a.rus.localeCompare(b.rus,"ru"));
  else if(srt==="az_en")list.sort((a,b)=>a.orig.localeCompare(b.orig,"en"));

  const grid=document.getElementById("grid");
  if(!list.length){{
    grid.innerHTML=`<div class="empty"><div class="eico">🔍</div><h3>Ничего не найдено</h3><p>Измените запрос или фильтр</p></div>`;
    return;
  }}
  grid.innerHTML=list.map(it=>{{
    const poster=it.poster||it.fbposter||PLACEHOLDER;
    const hasR=it.rating&&it.rating!=="Не оценено";
    return`<div class="card">
  <div class="poster">
    <img src="${{poster}}" alt="${{it.rus}}" loading="lazy"
         onerror="this.src='${{it.fbposter||PLACEHOLDER}}'">
    <div class="overlay">
      <div class="badges">
        <span class="sbadge ${{scls(it.status)}}">${{it.status}}</span>
        ${{hasR?`<span class="rbadge">⭐ ${{it.rating}}</span>`:""}}
      </div>
    </div>
  </div>
  <div class="cbody">
    <div>
      <div class="rus-title" title="${{it.rus}}">${{it.rus}}</div>
      <div class="orig-title" title="${{it.orig}}">${{it.orig}}</div>
    </div>
    <div class="pills">
      ${{it.ep?`<span class="pill p-ep">🎞 ${{it.ep}}</span>`:""}}
      ${{it.qual?`<span class="pill p-q">💿 ${{it.qual}}</span>`:""}}
    </div>
    <a class="dl-btn" href="${{it.folder}}" download="${{it.file}}">📥 Открыть торрент</a>
  </div>
</div>`;
  }}).join("");
}}
render();
</script>
</body>
</html>"""
    html = html.replace('{jdata}', jdata)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)

if __name__ == '__main__':
    main()
