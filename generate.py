#!/usr/bin/env python3
"""
Generatore della rassegna stampa.
Legge topics.json, interroga i feed RSS di Google News e produce index.html.
Solo libreria standard: nessuna dipendenza da installare.
"""

import json
import os
import re
import html
import urllib.request
import urllib.parse
import urllib.error
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


# ---------------------------------------------------------------------------
# Apprendimento dai voti (pollici su/giù inviati dalla pagina via Google Form)
# ---------------------------------------------------------------------------

STOPWORDS = set("""
il lo la i gli le un uno una di a da in con su per tra fra e o ma che chi cui non più del della dello dei degli delle al allo alla ai agli alle dal dallo dalla nel nello nella nei negli nelle sul sullo sulla sui sugli sulle come dopo prima contro anche ancora essere sono stato stata cosa ecco cosi così
the a an of to in on for and or but with from by at as is are was were be been it its this that these those new news how why what when
""".split())


def load_votes(csv_url):
    """Scarica i voti dal foglio Google pubblicato in CSV.

    Ritorna una lista di (datetime, titolo, voto) dove voto è +1 o -1.
    Per uno stesso titolo conta solo il voto più recente.
    """
    import csv as csvmod
    import io
    try:
        data = fetch(csv_url).decode("utf-8")
    except Exception as e:
        print(f"  [voti] impossibile scaricare i voti: {e}")
        return []
    rows = list(csvmod.reader(io.StringIO(data)))
    if len(rows) < 2:
        return []
    latest = {}
    for row in rows[1:]:
        if len(row) < 4:
            continue
        ts_raw, title, _tema, voto_raw = row[0], row[1].strip(), row[2], row[3].strip()
        if not title:
            continue
        try:
            ts = datetime.strptime(ts_raw.strip(), "%d/%m/%Y %H.%M.%S").replace(tzinfo=TZ)
        except ValueError:
            try:
                ts = datetime.strptime(ts_raw.strip(), "%m/%d/%Y %H:%M:%S").replace(tzinfo=TZ)
            except ValueError:
                ts = datetime.now(TZ)
        voto = 1 if "1" in voto_raw and "-" not in voto_raw else -1
        key = norm_title(title)[:80]
        if key not in latest or ts >= latest[key][0]:
            latest[key] = (ts, title, voto)
    return list(latest.values())


def build_profile(votes):
    """Profilo di interessi: peso per parola chiave, con decadimento temporale.

    Un voto recente pesa più di uno vecchio (dimezza ogni 45 giorni).
    """
    import math
    now = datetime.now(TZ)
    weights = {}
    for ts, title, voto in votes:
        age_days = max(0.0, (now - ts).total_seconds() / 86400)
        decay = 0.5 ** (age_days / 45)
        for tok in re.findall(r"[a-zà-ú0-9]+", title.lower()):
            if len(tok) < 3 or tok in STOPWORDS:
                continue
            weights[tok] = weights.get(tok, 0.0) + voto * decay
    return weights


def score_title(title, profile):
    if not profile:
        return 0.0
    s = 0.0
    for tok in set(re.findall(r"[a-zà-ú0-9]+", title.lower())):
        if tok in profile:
            s += max(-2.0, min(2.0, profile[tok]))
    return s


def collect(tema, ore_finestra, max_items, profile=None):
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
    # punteggio dai voti dell'utente
    for it in fresh:
        it["score"] = score_title(it["title"], profile or {})
    # scarta ciò che somiglia a notizie bocciate, se resta abbastanza materiale
    liked = [it for it in fresh if it["score"] > -2.0]
    if len(liked) >= 3:
        dropped = len(fresh) - len(liked)
        if dropped:
            print(f"  {dropped} scartate per voti negativi")
        fresh = liked
    # ordina: prima gli argomenti graditi, a parità la notizia più recente
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    fresh.sort(key=lambda it: (max(-3, min(3, round(it["score"]))), it["dt"] or epoch), reverse=True)
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


def build_recap(sezioni):
    """Riassunto in italiano delle novità salienti, generato con Google Gemini.

    Legge la chiave dal secret GEMINI_API_KEY. Se la chiave manca o la
    chiamata fallisce, la pagina viene comunque generata senza recap.
    """
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("  [recap] GEMINI_API_KEY assente: salto il riassunto AI")
        return None
    lines = []
    for s in sezioni:
        for it in s["items"][:5]:
            lines.append(f"[{s['nome']}] {it['title']} ({it['source']})")
    if not lines:
        return None
    prompt = (
        "Questa è la lista dei titoli di una rassegna stampa personale, "
        "raggruppati per tema tra parentesi quadre:\n\n" + "\n".join(lines) +
        "\n\nScrivi un breve recap in italiano delle novità salienti, come farebbe "
        "un giornalista in apertura di una rassegna stampa: tono sobrio ed elegante, "
        "120-170 parole, prosa fluida senza elenchi puntati, senza markdown e senza "
        "titolo. Tocca solo i temi per cui ci sono notizie davvero rilevanti, "
        "collegandoli con naturalezza; ignora i titoli marginali o ripetuti."
    )
    model = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
    body = json.dumps({
        "systemInstruction": {"parts": [{"text": (
            "Sei il curatore di una rassegna stampa in italiano, preciso e asciutto. "
            "Non inventare fatti: usa solo le informazioni presenti nei titoli."
        )}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 4096,
        },
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=body,
        headers={
            "x-goog-api-key": key,
            "Content-Type": "application/json",
            "User-Agent": UA,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read().decode("utf-8"))
        parts = data["candidates"][0]["content"]["parts"]
        text = "\n".join(p.get("text", "") for p in parts).strip()
        if len(text) < 40:
            print("  [recap] risposta troppo corta, la ignoro")
            return None
        print(f"  [recap] generato ({len(text)} caratteri)")
        return text
    except urllib.error.HTTPError as e:
        print(f"  [recap] fallito ({e.code}), procedo senza: {e.read()[:300]}")
        return None
    except Exception as e:
        print(f"  [recap] fallito, procedo senza: {e}")
        return None


def edizione(now):
    if now.hour < 10:
        return "Edizione del mattino"
    if now.hour < 16:
        return "Edizione di mezzogiorno"
    return "Edizione della sera"


def render(cfg, sezioni, now, recap=None):
    data_it = f"{GIORNI[now.weekday()]} {now.day} {MESI[now.month - 1]} {now.year}"
    tot = sum(len(s["items"]) for s in sezioni)

    def esc(s):
        return html.escape(s, quote=True)

    nav = "".join(
        f'<a href="#{esc(s["id"])}">{esc(s["nome"])}</a>' for s in sezioni
    )

    fb = cfg.get("feedback") or {}

    body_sections = []
    for s in sezioni:
        items = s["items"]
        if not items:
            inner = '<p class="empty">Nessuna notizia rilevante in questa edizione.</p>'
        else:
            lead, rest = items[0], items[1:]
            def meta(it, _s=s):
                lang_badge = f'<span class="lang">{it["lang"].upper()}</span>'
                vote = ""
                if fb.get("form_action"):
                    vote = (f'<span class="vote" data-t="{esc(it["title"])}" data-s="{esc(_s["id"])}">'
                            f'<button class="up" type="button" title="Mi interessa" aria-label="Mi interessa">&#128077;</button>'
                            f'<button class="down" type="button" title="Non mi interessa" aria-label="Non mi interessa">&#128078;</button>'
                            f'</span>')
                return (f'<span class="src">{esc(it["source"])}</span>'
                        f'<span class="dot">·</span><span class="when">{esc(rel_time(it["dt"], now))}</span>'
                        f'{lang_badge}{vote}')
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

    recap_html = ""
    if recap:
        paras = "".join(f"<p>{esc(p.strip())}</p>" for p in recap.split("\n") if p.strip())
        recap_html = (
            '<section class="recap" aria-label="In sintesi">'
            '<p class="recap-label">In sintesi</p>'
            f'{paras}'
            '<p class="recap-note">Riassunto generato con AI a partire dai titoli di questa edizione.</p>'
            '</section>'
        )

    vote_js = ""
    if fb.get("form_action"):
        fb_json = json.dumps({
            "action": fb["form_action"], "t": fb["campo_titolo"],
            "s": fb["campo_tema"], "v": fb["campo_voto"],
        })
        vote_js = (
            "<script>\n(function(){\n"
            f"var FB={fb_json};\n"
            'var KEY="rassegna-voti";\n'
            'function st(){try{return JSON.parse(localStorage.getItem(KEY)||"{}")}catch(e){return {}}}\n'
            "function save(m){try{localStorage.setItem(KEY,JSON.stringify(m))}catch(e){}}\n"
            'function norm(t){return t.toLowerCase().replace(/[^a-z0-9\\u00e0\\u00e8\\u00e9\\u00ec\\u00f2\\u00f9]+/g,"").slice(0,80)}\n'
            'function apply(){var m=st();document.querySelectorAll(".vote").forEach(function(v){var k=norm(v.dataset.t);var val=m[k];'
            'v.querySelector(".up").classList.toggle("on",val===1);'
            'v.querySelector(".down").classList.toggle("on",val===-1)})}\n'
            'document.addEventListener("click",function(e){\n'
            ' var b=e.target.closest(".vote button");if(!b)return;\n'
            ' var wrap=b.closest(".vote");var val=b.classList.contains("up")?1:-1;\n'
            " var fd=new FormData();fd.append(FB.t,wrap.dataset.t);fd.append(FB.s,wrap.dataset.s);"
            'fd.append(FB.v,val>0?"+1":"-1");\n'
            ' fetch(FB.action,{method:"POST",mode:"no-cors",body:fd}).catch(function(){});\n'
            " var m=st();m[norm(wrap.dataset.t)]=val;save(m);apply();\n"
            "});\napply();\n})();\n</script>"
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
.recap {{
  margin: 30px 0 4px; padding: 22px 26px;
  background: var(--card); border: 1px solid var(--rule); border-radius: 10px;
}}
.recap-label {{
  font-size: 11px; font-weight: 600; letter-spacing: .22em; text-transform: uppercase;
  color: var(--accent); margin-bottom: 10px;
}}
.recap p:not(.recap-label):not(.recap-note) {{
  font-family: Fraunces, Georgia, serif; font-size: 17.5px; line-height: 1.6;
}}
.recap p + p {{ margin-top: 10px; }}
.recap-note {{ margin-top: 14px; font-size: 11.5px; color: var(--muted); }}
.vote {{ display: inline-flex; gap: 2px; margin-left: 6px; }}
.vote button {{
  border: 0; background: none; cursor: pointer; font-size: 14px;
  line-height: 1; padding: 2px 4px; border-radius: 6px;
  filter: grayscale(1); opacity: .45; transition: all .15s;
}}
.vote button:hover {{ filter: none; opacity: 1; background: var(--badge); }}
.vote button.on {{ filter: none; opacity: 1; background: var(--badge); }}
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
  {recap_html}
  {"".join(body_sections)}
  <footer>
    <p>{esc(cfg["sottotitolo"])}. Generata automaticamente tre volte al giorno dai feed di Google News.</p>
    <p>Vota le notizie con &#128077; e &#128078;: le prossime edizioni impareranno dai tuoi gusti.</p>
  </footer>
</div>
{vote_js}
</body>
</html>
"""


def main():
    cfg = json.loads((BASE / "topics.json").read_text(encoding="utf-8"))
    now = datetime.now(TZ)
    fb = cfg.get("feedback") or {}
    votes = load_votes(fb["voti_csv"]) if fb.get("voti_csv") else []
    profile = build_profile(votes)
    print(f"[voti] {len(votes)} voti letti, {len(profile)} parole nel profilo")
    sezioni = []
    for tema in cfg["temi"]:
        print(f"[{tema['id']}] raccolta…")
        items = collect(tema, cfg.get("ore_finestra", 72), cfg.get("max_per_tema", 8), profile)
        print(f"  {len(items)} notizie")
        sezioni.append({**tema, "items": items})
    recap = build_recap(sezioni)
    out = render(cfg, sezioni, now, recap)
    (BASE / "index.html").write_text(out, encoding="utf-8")
    print(f"index.html generato ({len(out)} byte)")


if __name__ == "__main__":
    main()
