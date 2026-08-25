#!/usr/bin/env python3
"""
Generatore della rassegna stampa.
Legge topics.json, interroga i feed RSS di Google News e produce index.html.
Solo libreria standard: nessuna dipendenza da installare.
"""

import json
import re
import html
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).parent
TZ = ZoneInfo("Europe/Rome")

MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
        "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]
GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"


def feed_url(q, lang):
    if lang == "it":
        params = {"q": q, "hl": "it", "gl": "IT", "ceid": "IT:it"}
    else:
        params = {"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def parse_feed(data, lang):
    items = []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return items
    for it in root.iterfind("./channel/item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        source = (it.findtext("source") or "").strip()
        pub = it.findtext("pubDate")
        if not title or not link:
            continue
        # Google News accoda " - Testata" al titolo: lo rimuoviamo se coincide con <source>
        if source and title.endswith(" - " + source):
            title = title[: -(len(source) + 3)].strip()
        else:
            title = re.sub(r"\s+-\s+[^-]{2,40}$", "", title).strip() or title
        try:
            dt = parsedate_to_datetime(pub) if pub else None
            if dt and dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            dt = None
        items.append({"title": title, "link": link, "source": source or "—",
                      "dt": dt, "lang": lang})
    return items


def norm_title(t):
    return re.sub(r"[^a-z0-9àèéìòù]+", "", t.lower())


def collect(tema, ore_finestra, max_items):
    pool = []
    for q in tema["query"]:
        try:
            pool += parse_feed(fetch(feed_url(q["q"], q["lang"])), q["lang"])
        except Exception as e:
            print(f"  [warn] feed fallito per {tema['id']} ({q['lang']}): {e}")
    # filtri per parole chiave sul titolo (opzionali, definiti in topics.json)
    richiedi = tema.get("richiedi")
    escludi = tema.get("escludi")
    protetto = tema.get("protetto")

    def ok(it):
        t = it["title"].lower()
        if richiedi and not re.search(richiedi, t, re.I):
            return False
        if escludi and re.search(escludi, t, re.I):
            if not (protetto and re.search(protetto, t, re.I)):
                return False
        return True

    pool = [it for it in pool if ok(it)]
    # dedup per titolo normalizzato
    seen, unique = set(), []
    for it in sorted(pool, key=lambda x: x["dt"] or datetime(1970, 1, 1, tzinfo=timezone.utc), reverse=True):
        key = norm_title(it["title"])[:80]
        if key and key not in seen:
            seen.add(key)
            unique.append(it)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ore_finestra)
    fresh = [it for it in unique if it["dt"] and it["dt"] >= cutoff]
    if len(fresh) < 3:  # tema con poche notizie: mostra comunque le più recenti
        fresh = unique[:3]
    return fresh[:max_items]


def rel_time(dt, now):
    if not dt:
        return ""
    delta = now - dt.astimezone(TZ)
    h = delta.total_seconds() / 3600
    if h < 1:
        return f"{max(1, int(delta.total_seconds() // 60))} min fa"
    if h < 24:
        return f"{int(h)} h fa"
    d = int(h // 24)
    if d == 1:
        return "ieri"
    if d < 7:
        return f"{d} giorni fa"
    return f"{dt.astimezone(TZ).day} {MESI[dt.astimezone(TZ).month - 1]}"


def edizione(now):
    if now.hour < 10:
        return "Edizione del mattino"
    if now.hour < 16:
        return "Edizione di mezzogiorno"
    return "Edizione della sera"


def render(cfg, sezioni, now):
    data_it = f"{GIORNI[now.weekday()]} {now.day} {MESI[now.month - 1]} {now.year}"
    tot = sum(len(s["items"]) for s in sezioni)

    def esc(s):
        return html.escape(s, quote=True)

    nav = "".join(
        f'<a href="#{esc(s["id"])}">{esc(s["nome"])}</a>' for s in sezioni
    )

    body_sections = []
    for s in sezioni:
        items = s["items"]
        if not items:
            inner = '<p class="empty">Nessuna notizia rilevante in questa edizione.</p>'
        else:
            lead, rest = items[0], items[1:]
            def meta(it):
                lang_badge = f'<span class="lang">{it["lang"].upper()}</span>'
                return (f'<span class="src">{esc(it["source"])}</span>'
                        f'<span class="dot">·</span><span class="when">{esc(rel_time(it["dt"], now))}</span>'
                        f'{lang_badge}')
            inner = (
                f'<article class="lead"><a href="{esc(lead["link"])}" target="_blank" rel="noopener">'
                f'<h3>{esc(lead["title"])}</h3></a><p class="meta">{meta(lead)}</p></article>'
            )
            if rest:
                inner += "<ul>" + "".join(
                    f'<li><a href="{esc(it["link"])}" target="_blank" rel="noopener">{esc(it["title"])}</a>'
                    f'<p class="meta">{meta(it)}</p></li>'
                    for it in rest
                ) + "</ul>"
        body_sections.append(
            f'<section id="{esc(s["id"])}">'
            f'<header><h2>{esc(s["nome"])}</h2><p class="desc">{esc(s["descrizione"])}</p></header>'
            f'{inner}</section>'
        )

    gen_ts = now.strftime("%H:%M")
    return f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(cfg["titolo"])} — {esc(data_it)}</title>
<meta name="description" content="Rassegna stampa automatica: {esc(cfg["sottotitolo"])}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root {{
  --bg: #faf7f2; --ink: #1c1a17; --muted: #6f6a61; --rule: #d8d2c6;
  --accent: #8a3b12; --card: #ffffff; --badge: #eee8dd;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #16150f; --ink: #ece7dc; --muted: #9a948a; --rule: #38352c;
    --accent: #e08a54; --card: #1e1c15; --badge: #2a2820;
  }}
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  background: var(--bg); color: var(--ink);
  font-family: Inter, -apple-system, "Segoe UI", sans-serif;
  line-height: 1.55; -webkit-font-smoothing: antialiased;
}}
.wrap {{ max-width: 760px; margin: 0 auto; padding: 0 20px 64px; }}
.masthead {{ text-align: center; padding: 44px 0 18px; border-bottom: 3px double var(--rule); }}
.masthead .kicker {{
  font-size: 12px; letter-spacing: .22em; text-transform: uppercase; color: var(--muted);
}}
.masthead h1 {{
  font-family: Fraunces, Georgia, serif; font-weight: 700;
  font-size: clamp(40px, 8vw, 64px); letter-spacing: -.01em; margin: 6px 0 8px;
}}
.masthead .date {{ font-size: 14px; color: var(--muted); }}
.masthead .date b {{ color: var(--ink); font-weight: 600; }}
nav {{
  display: flex; flex-wrap: wrap; gap: 4px 18px; justify-content: center;
  padding: 12px 0; border-bottom: 1px solid var(--rule);
  font-size: 13px; font-weight: 500;
}}
nav a {{ color: var(--muted); text-decoration: none; }}
nav a:hover {{ color: var(--accent); }}
section {{ padding: 34px 0 6px; border-bottom: 1px solid var(--rule); }}
section > header {{ display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 18px; }}
section h2 {{
  font-family: Fraunces, Georgia, serif; font-size: 26px; font-weight: 600;
}}
section .desc {{ font-size: 13px; color: var(--muted); }}
a {{ color: inherit; }}
.lead a {{ text-decoration: none; }}
.lead h3 {{
  font-family: Fraunces, Georgia, serif; font-size: clamp(21px, 3.4vw, 27px);
  font-weight: 600; line-height: 1.25; margin-bottom: 6px;
}}
.lead a:hover h3 {{ color: var(--accent); }}
.meta {{ font-size: 12.5px; color: var(--muted); display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }}
.meta .src {{ font-weight: 600; }}
.meta .lang {{
  font-size: 10px; font-weight: 600; letter-spacing: .08em;
  background: var(--badge); border-radius: 4px; padding: 1px 6px;
}}
ul {{ list-style: none; margin-top: 18px; }}
li {{ padding: 12px 0; border-top: 1px solid var(--rule); }}
li a {{ font-size: 16px; font-weight: 500; text-decoration: none; line-height: 1.4; }}
li a:hover {{ color: var(--accent); }}
li .meta {{ margin-top: 4px; }}
.empty {{ color: var(--muted); font-style: italic; padding: 8px 0 20px; }}
footer {{ padding-top: 26px; font-size: 12.5px; color: var(--muted); text-align: center; }}
footer a {{ color: var(--muted); }}
</style>
</head>
<body>
<div class="wrap">
  <header class="masthead">
    <p class="kicker">{esc(edizione(now))}</p>
    <h1>{esc(cfg["titolo"])}</h1>
    <p class="date"><b>{esc(data_it)}</b> &nbsp;·&nbsp; aggiornata alle {gen_ts} &nbsp;·&nbsp; {tot} notizie</p>
  </header>
  <nav>{nav}</nav>
  {"".join(body_sections)}
  <footer>
    <p>{esc(cfg["sottotitolo"])}. Generata automaticamente tre volte al giorno dai feed di Google News.</p>
  </footer>
</div>
</body>
</html>
"""


def main():
    cfg = json.loads((BASE / "topics.json").read_text(encoding="utf-8"))
    now = datetime.now(TZ)
    sezioni = []
    for tema in cfg["temi"]:
        print(f"[{tema['id']}] raccolta…")
        items = collect(tema, cfg.get("ore_finestra", 72), cfg.get("max_per_tema", 8))
        print(f"  {len(items)} notizie")
        sezioni.append({**tema, "items": items})
    out = render(cfg, sezioni, now)
    (BASE / "index.html").write_text(out, encoding="utf-8")
    print(f"index.html generato ({len(out)} byte)")


if __name__ == "__main__":
    main()
