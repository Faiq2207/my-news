#!/usr/bin/env python3
"""
News bot for your website.

  python news.py headlines   -> fetch latest headlines from all channels (runs every hour)
  python news.py articles    -> write 10 local + 10 international articles (runs twice a day)

Everything older than `keep_days` (default 3) is deleted automatically on every run.
"""
import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, urlparse

import feedparser
import requests

sys.stdout.reconfigure(line_buffering=True)  # show progress live in the GitHub log

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
HEADLINES_FILE = os.path.join(DATA, "headlines.json")
ARTICLES_FILE = os.path.join(DATA, "articles.json")
STATUS_FILE = os.path.join(DATA, "status.json")

with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
    CONFIG = json.load(f)

KEEP_DAYS = int(CONFIG.get("keep_days", 3))
ARCHIVE_DAYS = int(CONFIG.get("story_pages_keep_days", 365))
ARCHIVE_FILE = os.path.join(DATA, "archive.json")
N_NEWS = int(CONFIG.get("articles_news_per_section", 8))
N_SPORTS = int(CONFIG.get("articles_sports_per_section", 2))
SITE_NAME = CONFIG.get("site_name", "My News Channel")
GEMINI_MODELS = [m for m in os.environ.get(
    "GEMINI_MODELS", "gemini-flash-latest,gemini-flash-lite-latest,gemini-2.5-flash-lite").split(",") if m.strip()]
SITE_URL = CONFIG.get("site_url", "https://faiq2207.github.io/my-news/").rstrip("/") + "/"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NewsDeskBot/1.0; +https://github.com)"}
MAX_PER_FEED = 40
MAX_HEADLINES = 2000

# ---------------------------------------------------------------------------
# Sources. "feeds" are tried first. If they all fail, the script falls back to
# Google News results for that website, so every channel still shows up.
# Add or remove channels here.
# ---------------------------------------------------------------------------
SOURCES = [
    # International
    {"name": "BBC News", "region": "international", "domain": "bbc.com", "alt_domains": ["bbc.co.uk"],
     "feeds": ["https://feeds.bbci.co.uk/news/world/rss.xml",
               "https://feeds.bbci.co.uk/sport/rss.xml"]},
    {"name": "CNN", "region": "international", "domain": "cnn.com",
     "feeds": ["http://rss.cnn.com/rss/edition_world.rss",
               "http://rss.cnn.com/rss/edition_sport.rss"]},
    {"name": "Al Jazeera", "region": "international", "domain": "aljazeera.com",
     "feeds": ["https://www.aljazeera.com/xml/rss/all.xml"]},
    {"name": "Fox News", "region": "international", "domain": "foxnews.com",
     "feeds": ["https://moxie.foxnews.com/google-publisher/world.xml",
               "https://moxie.foxnews.com/google-publisher/sports.xml"]},
    {"name": "Sky News", "region": "international", "domain": "news.sky.com",
     "feeds": ["https://feeds.skynews.com/feeds/rss/world.xml"]},
    {"name": "France 24", "region": "international", "domain": "france24.com",
     "feeds": ["https://www.france24.com/en/rss"]},
    {"name": "DW", "region": "international", "domain": "dw.com",
     "feeds": ["https://rss.dw.com/rdf/rss-en-all"]},
    {"name": "CNBC", "region": "international", "domain": "cnbc.com",
     "feeds": ["https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100727362"]},
    {"name": "Euronews", "region": "international", "domain": "euronews.com",
     "feeds": ["https://www.euronews.com/rss"]},
    {"name": "NHK World", "region": "international", "domain": "nhk.or.jp", "feeds": []},

    # Pakistan
    {"name": "Geo News", "region": "local", "domain": "geo.tv", "feeds": []},
    {"name": "ARY News", "region": "local", "domain": "arynews.tv",
     "feeds": ["https://arynews.tv/feed/"]},
    {"name": "Dunya News", "region": "local", "domain": "dunyanews.tv", "feeds": []},
    {"name": "Samaa", "region": "local", "domain": "samaa.tv",
     "feeds": ["https://www.samaa.tv/feed/"]},
    {"name": "Express Tribune", "region": "local", "domain": "tribune.com.pk",
     "feeds": ["https://tribune.com.pk/feed/home",
               "https://tribune.com.pk/feed/sports"]},
    {"name": "Express News", "region": "local", "domain": "express.pk", "feeds": []},
    {"name": "92 News", "region": "local", "domain": "92newshd.tv", "feeds": []},
    {"name": "Hum News", "region": "local", "domain": "humnews.pk", "feeds": []},
    {"name": "Aaj News", "region": "local", "domain": "aaj.tv", "feeds": []},
    {"name": "GNN", "region": "local", "domain": "gnnhd.tv", "feeds": []},
    {"name": "PTV News", "region": "local", "domain": "ptv.com.pk", "feeds": []},
    {"name": "Dawn", "region": "local", "domain": "dawn.com",
     "feeds": ["https://www.dawn.com/feeds/home",
               "https://www.dawn.com/feeds/sport"]},
]

SPORTS_EN = re.compile(
    r"\b(cricket|football|soccer|t20i?|odi|test series|wicket|wickets|innings|pcb|icc|fifa|uefa|"
    r"premier league|la liga|champions league|nba|nfl|mlb|nhl|tennis|wimbledon|olympics?|"
    r"grand prix|formula 1|f1|golf|boxing|ufc|hockey|squash|rugby|athletics|psl|ipl|"
    r"world cup|asia cup|babar|rizwan|shaheen|kohli|messi|ronaldo|batter|bowler|striker|goalkeeper)\b",
    re.I)
SPORTS_UR = re.compile(r"(کرکٹ|فٹبال|میچ|ٹی20|ٹی ٹوئنٹی|ورلڈ کپ|ہاکی|اسکواش|بابر اعظم|پی سی بی|وکٹ)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def clean(text, limit=300):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def is_sports(title, summary, from_sports_feed):
    text = f"{title} {summary}"
    return from_sports_feed or bool(SPORTS_EN.search(text) or SPORTS_UR.search(text))


def entry_time(entry, fallback):
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            dt = datetime(*t[:6], tzinfo=timezone.utc)
            return min(dt, fallback)  # never in the future
    return fallback


def gnews_url(domain):
    q = quote_plus(f"site:{domain} when:1d")
    return f"https://news.google.com/rss/search?q={q}&hl=en-PK&gl=PK&ceid=PK:en"


def on_site(link, src):
    host = urlparse(link).netloc.lower()
    allowed = [src["domain"]] + src.get("alt_domains", [])
    return any(host == d or host.endswith("." + d) for d in allowed) and "utm_medium=cpc" not in link


def parse_feed(url, src, sports_feed=False, google=False):
    resp = requests.get(url, headers=HEADERS, timeout=25)
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    if not feed.entries:
        raise ValueError("feed has no items")
    fetched = now_utc()
    items = []
    for e in feed.entries[:MAX_PER_FEED]:
        title = clean(e.get("title", ""), 220)
        link = e.get("link", "")
        if not title or not link:
            continue
        if not google and not on_site(link, src):
            continue  # sponsored or off-site link (for example an ad inside a feed)
        if google:  # Google News titles end with " - Source Name"
            title = re.sub(r"\s+-\s+[^-]{2,60}$", "", title)
            summary = ""
        else:
            summary = clean(e.get("summary", "") or e.get("description", ""))
        items.append({
            "id": hashlib.sha1(link.encode()).hexdigest()[:16],
            "source": src["name"],
            "region": src["region"],
            "category": "sports" if is_sports(title, summary, sports_feed) else "news",
            "title": title,
            "summary": summary,
            "link": link,
            "published": iso(entry_time(e, fetched)),
            "fetched": iso(fetched),
        })
    return items


def prune(items, field):
    cutoff = now_utc() - timedelta(days=KEEP_DAYS)
    return [i for i in items if parse_iso(i[field]) >= cutoff]


# ---------------------------------------------------------------------------
# Mode 1: headlines
# ---------------------------------------------------------------------------
def run_headlines():
    store = {h["id"]: h for h in load(HEADLINES_FILE, [])}
    status = []
    for src in SOURCES:
        got, via, errors = [], "rss", []
        for url in src["feeds"]:
            try:
                got += parse_feed(url, src, sports_feed="sport" in url)
            except Exception as ex:  # one bad feed must not stop the rest
                errors.append(f"{url}: {ex}")
        if not got:
            via = "google-news"
            try:
                got = parse_feed(gnews_url(src["domain"]), src, google=True)
            except Exception as ex:
                errors.append(f"google-news: {ex}")
        for h in got:
            store.setdefault(h["id"], h)
        status.append({"source": src["name"], "region": src["region"],
                       "items": len(got), "via": via if got else "failed"})
        print(f"{src['name']:<16} {len(got):>3} items via {via if got else 'FAILED'}")
        for err in errors:
            print(f"    note: {err[:160]}")

    items = prune(list(store.values()), "published")
    items.sort(key=lambda h: h["published"], reverse=True)
    save(HEADLINES_FILE, items[:MAX_HEADLINES])
    save(STATUS_FILE, {"updated": iso(now_utc()), "sources": status})
    print(f"Saved {min(len(items), MAX_HEADLINES)} headlines (last {KEEP_DAYS} days).")


# ---------------------------------------------------------------------------
# Mode 2: articles
# ---------------------------------------------------------------------------
PROMPT = """You are a careful news writer for the website "{site}".
Below are numbered news and sports items collected in the last few hours from other news websites ({scope}).

TASK
Pick the {n_news} most important DIFFERENT news stories and the {n_sports} most important DIFFERENT sports stories.
{focus}Prefer stories reported by several sources. Write one short original article for each.

STRICT RULES
1. Use ONLY facts that appear in the items below. Never add facts, numbers, names, dates, quotes or background from your own knowledge.
2. Write every number with digits, exactly as it appears in the items.
3. Say who reported each claim, for example "according to Dawn" or "BBC reported".
4. If sources disagree, give both versions and name who reported each.
5. Write in your own words in English. Never copy a full sentence from an item.
6. Neutral tone. No opinions, guesses or predictions.
7. 120 to 220 words per article, in 2 to 4 short paragraphs.
8. "sources" lists the IDs of every item you used. At least one; two or more when possible.
9. "category" is "news" for stories built from N items and "sports" for stories built from S items.

Return only JSON in this shape:
{{"articles": [{{"category": "news", "title": "...", "summary": "one sentence",
  "paragraphs": ["...", "..."], "sources": ["N3", "N17"]}}]}}

{items}
"""

NUM = re.compile(r"\d[\d,.]*\d|\d")


def ask_gemini(prompt, key):
    """Try each free model in turn. Short waits keep a run under about 10 minutes."""
    for model in GEMINI_MODELS:
        model = model.strip()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        config = {"temperature": 0.2, "responseMimeType": "application/json",
                  "thinkingConfig": {"thinkingBudget": 0}}
        for attempt in range(3):
            body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": config}
            try:
                r = requests.post(url, json=body, timeout=120,
                                  headers={"x-goog-api-key": key, "Content-Type": "application/json"})
            except requests.exceptions.RequestException as ex:
                print(f"  {model}: connection problem ({type(ex).__name__}), retrying…")
                time.sleep(10)
                continue
            if r.status_code == 400 and "thinkingConfig" in config:
                config = {k: v for k, v in config.items() if k != "thinkingConfig"}
                continue
            if r.status_code == 404:
                print(f"  {model}: not available, trying the next model")
                break
            if r.status_code == 429 and "quota" in r.text.lower() and "per day" in r.text.lower():
                print(f"  {model}: daily free limit used up, trying the next model")
                break
            if r.status_code in (429, 500, 502, 503, 504):
                wait = 20 * (attempt + 1)
                print(f"  {model}: busy ({r.status_code}), waiting {wait}s…")
                time.sleep(wait)
                continue
            if r.status_code >= 400:
                print(f"  {model}: error {r.status_code}: {r.text[:200]}")
                break
            try:
                parts = r.json()["candidates"][0]["content"]["parts"]
                text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
                text = re.sub(r"^```(?:json)?|```$", "", text).strip()
                return json.loads(text)
            except (KeyError, IndexError, ValueError) as ex:
                print(f"  {model}: unreadable answer ({type(ex).__name__}), retrying…")
                continue
        print(f"  {model}: giving up, trying the next model")
    raise RuntimeError("all AI models were busy, it will try again at the next scheduled run")


def numbers_ok(article_text, ref_items):
    """Every number in the article must appear in at least one referenced item."""
    ref_text = " ".join(f"{h['title']} {h['summary']} {h['published']} {h['source']}" for h in ref_items)
    ref_text += " " + " ".join(s["name"] for s in SOURCES)
    ref_nums = {n.replace(",", "").rstrip(".") for n in NUM.findall(ref_text)}
    missing = [n for n in NUM.findall(article_text)
               if n.replace(",", "").rstrip(".") not in ref_nums]
    return missing


def build_articles(result, ref, cat, limit, now, region):
    out = []
    for a in result.get("articles", []):
        refs = [ref[r] for r in a.get("sources", []) if r in ref]
        paras = [p.strip() for p in a.get("paragraphs", []) if p and p.strip()]
        title = (a.get("title") or "").strip()
        if not refs or not paras or not title or len(out) >= limit:
            continue
        missing = numbers_ok(" ".join([title, a.get("summary", "")] + paras), refs)
        if missing:
            print(f"  Rejected (numbers not found in sources {missing}): {title[:70]}")
            continue
        out.append({
            "id": hashlib.sha1(f"{now.isoformat()}{region}{title}".encode()).hexdigest()[:16],
            "region": region,
            "category": cat,
            "title": title,
            "summary": (a.get("summary") or "").strip(),
            "paragraphs": paras,
            "sources": [{"source": h["source"], "title": h["title"], "link": h["link"],
                         "published": h["published"], "used_text": h["summary"]} for h in refs],
            "created": iso(now),
        })
    return out


def write_section(region, key, headlines, now):
    recent = [h for h in headlines if h["region"] == region
              and now - parse_iso(h["published"]) <= timedelta(hours=12)]
    if len(recent) < 40:
        recent = [h for h in headlines if h["region"] == region
                  and now - parse_iso(h["published"]) <= timedelta(hours=24)]
    scope = "Pakistan" if region == "local" else "international"
    focus = ("Only choose stories about Pakistan or directly involving Pakistan.\n" if region == "local"
             else "Choose world stories; skip stories that are mainly about Pakistan.\n")
    ref, blocks = {}, []
    for cat, prefix, cap in (("news", "N", 120), ("sports", "S", 60)):
        group = [h for h in recent if h["category"] == cat][:cap]
        lines = []
        for i, h in enumerate(group, 1):
            rid = f"{prefix}{i}"
            ref[rid] = h
            text = f"[{rid}] ({h['source']}, {h['published'][:16].replace('T', ' ')} UTC) {h['title']}"
            if h["summary"]:
                text += f" | {h['summary']}"
            lines.append(text)
        blocks.append(f"{cat.upper()} ITEMS (IDs start with {prefix})\n" + ("\n".join(lines) or "(none)"))
    if not ref:
        print(f"  No {region} headlines yet, skipping.")
        return []
    prompt = PROMPT.format(site=SITE_NAME, scope=scope, n_news=N_NEWS, n_sports=N_SPORTS,
                           focus=focus, items="\n\n".join(blocks))
    try:
        result = ask_gemini(prompt, key)
    except Exception as ex:
        print(f"  Could not write {region} stories: {ex}")
        return []
    out = []
    for cat, limit in (("news", N_NEWS), ("sports", N_SPORTS)):
        part = {"articles": [a for a in result.get("articles", []) if (a.get("category") or "news") == cat]}
        got = build_articles(part, ref, cat, limit, now, region)
        print(f"  {region} {cat}: {len(got)} written")
        out += got
    return out


def run_articles():
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        sys.exit("GEMINI_API_KEY is missing. Add it under Settings > Secrets and variables > Actions.")
    headlines = load(HEADLINES_FILE, [])
    now = now_utc()
    new = []
    for region in ("local", "international"):
        print(f"Writing {region} articles…")
        new += write_section(region, key, headlines, now)
    for a in new:
        a["url"] = f"a/{slug_for(a)}.html"
    old = load(ARTICLES_FILE, [])
    articles = prune(new + old, "created")
    articles.sort(key=lambda a: a["created"], reverse=True)
    save(ARTICLES_FILE, articles)
    print(f"Saved {len(articles)} articles (last {KEEP_DAYS} days) for the homepage.")
    build_site(articles, new)


# ---------------------------------------------------------------------------
# Search-friendly pages: one page per story (kept for a year), section pages,
# about page, sitemaps (normal + Google News), homepage SEO
# ---------------------------------------------------------------------------
PAGE_DIR = os.path.join(ROOT, "a")
PKT = timezone(timedelta(hours=5))
SECTIONS = {  # file name, heading, description, filter
    "pakistan": ("Pakistan news", "Latest Pakistan news stories, written from reports by Dawn, Geo, ARY, Express Tribune and other channels.",
                 lambda a: a["region"] == "local" and a["category"] == "news"),
    "world": ("World news", "Latest international news stories, written from reports by BBC, Al Jazeera, DW, CNN and other channels.",
              lambda a: a["region"] == "international" and a["category"] == "news"),
    "sports": ("Sports news", "Latest cricket, football and other sports stories from Pakistan and around the world.",
               lambda a: a["category"] == "sports"),
}


def esc(t):
    return html.escape(t or "", quote=True)


def slug_for(a):
    words = re.sub(r"[^a-z0-9]+", "-", a["title"].lower()).strip("-")
    words = "-".join(words.split("-")[:9])
    return f"{a['created'][:10]}-{words}-{a['id'][:4]}"


def pkt_time(s):
    return parse_iso(s).astimezone(PKT).strftime("%-d %B %Y, %-I:%M %p PKT")


CSS = """:root{--paper:#f4f7f4;--ink:#17212b;--muted:#5f6b74;--line:#d6ded8;--green:#0e6b4f;color-scheme:light;
box-sizing:border-box;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}
@media (prefers-color-scheme:dark){:root{--paper:#10171c;--ink:#e6ece8;--muted:#9aa8a1;--line:#26332d;--green:#5fc29c;color-scheme:dark}}
*,*::before,*::after{box-sizing:inherit}
body{margin:0;background:var(--paper);color:var(--ink);font-family:"Source Serif 4",Georgia,serif;line-height:1.7}
.w{max-width:720px;margin:0 auto;padding:24px 20px 60px} a{color:var(--green)}
.nav{font-family:system-ui,sans-serif;font-size:.9rem;display:flex;flex-wrap:wrap;gap:6px 16px;align-items:baseline}
.nav .home{font-weight:700;text-decoration:none;font-size:1rem;margin-right:auto}
h1{font-family:system-ui,sans-serif;font-size:clamp(1.6rem,4vw,2.3rem);line-height:1.2;margin:18px 0 6px}
h2{font-family:system-ui,sans-serif;font-size:1.1rem;margin:28px 0 8px}
.by,.m,.note{font-family:system-ui,sans-serif;color:var(--muted);font-size:.85rem}
.list{list-style:none;padding:0;margin:0} .list li{border-top:1px solid var(--line);padding:12px 0}
.list a{font-family:system-ui,sans-serif;font-weight:600;text-decoration:none;color:var(--ink)} .list p{margin:4px 0 0;color:var(--muted)}
.refs{border-top:1px solid var(--line);margin-top:28px;padding-top:12px} .refs li{margin:8px 0}
footer{border-top:1px solid var(--line);margin-top:32px;padding-top:12px}
:focus-visible{outline:2px solid var(--green);outline-offset:2px}"""


def shell(title, desc, canonical, body, depth=0, extra_head="", og_type="website"):
    up = "../" * depth
    nav = (f'<nav class="nav" aria-label="Sections"><a class="home" href="{up}./">{esc(SITE_NAME)}</a>'
           + "".join(f'<a href="{up}{k}.html">{esc(v[0])}</a>' for k, v in SECTIONS.items())
           + f'<a href="{up}about.html">About</a></nav>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1">
<link rel="canonical" href="{canonical}">
<meta property="og:site_name" content="{esc(SITE_NAME)}"><meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}"><meta property="og:url" content="{canonical}"><meta property="og:type" content="{og_type}">
<meta name="twitter:card" content="summary">
{extra_head}
<style>{CSS}</style></head>
<body><div class="w">
{nav}
{body}
<footer class="note">Stories are written with AI assistance using only facts from the reports listed under each one. Headlines and links belong to their publishers. <a href="{up}about.html">How we work</a></footer>
</div></body></html>"""


def story_page(a, related):
    url = SITE_URL + a["url"]
    refs = "".join(
        f'<li><a href="{esc(s["link"])}" rel="nofollow noopener" target="_blank">{esc(s["title"])}</a>'
        f'<div class="m">{esc(s["source"])}</div></li>' for s in a["sources"])
    body_txt = "".join(f"<p>{esc(p)}</p>" for p in a["paragraphs"])
    sec = "sports" if a["category"] == "sports" else ("pakistan" if a["region"] == "local" else "world")
    ld = [{"@context": "https://schema.org", "@type": "NewsArticle", "headline": a["title"][:110],
           "datePublished": a["created"], "dateModified": a["created"], "description": a.get("summary", ""),
           "articleSection": SECTIONS[sec][0], "isAccessibleForFree": True,
           "author": {"@type": "Organization", "name": SITE_NAME, "url": SITE_URL},
           "publisher": {"@type": "Organization", "name": SITE_NAME, "url": SITE_URL},
           "mainEntityOfPage": url},
          {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
              {"@type": "ListItem", "position": 1, "name": SITE_NAME, "item": SITE_URL},
              {"@type": "ListItem", "position": 2, "name": SECTIONS[sec][0], "item": f"{SITE_URL}{sec}.html"},
              {"@type": "ListItem", "position": 3, "name": a["title"]}]}]
    rel = "".join(f'<li><a href="../{esc(r["url"])}">{esc(r["title"])}</a></li>' for r in related)
    body = f"""<p class="by"><a href="../{sec}.html">{esc(SECTIONS[sec][0])}</a></p>
<article><h1>{esc(a["title"])}</h1>
<div class="by">{esc(SITE_NAME)} news desk, <time datetime="{a["created"]}">{pkt_time(a["created"])}</time></div>
{body_txt}
<div class="refs"><h2>Sources</h2><p class="note">This story uses only facts from these reports.</p><ol>{refs}</ol></div></article>
{f'<h2>More {esc(SECTIONS[sec][0].lower())}</h2><ul class="list">{rel}</ul>' if rel else ""}"""
    head = f'<meta property="article:published_time" content="{a["created"]}">\n<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>'
    return shell(f"{a['title']} | {SITE_NAME}", a.get("summary") or a["title"], url, body, depth=1,
                 extra_head=head, og_type="article")


def list_items(items, up=""):
    return "".join(
        f'<li><a href="{up}{esc(a["url"])}">{esc(a["title"])}</a><p>{esc(a.get("summary", ""))}</p>'
        f'<div class="m">{pkt_time(a["created"])}</div></li>' for a in items)


def build_site(articles, new=()):
    archive = {a["id"]: a for a in load(ARCHIVE_FILE, [])}
    for a in list(new) + list(articles):
        a.setdefault("url", f"a/{slug_for(a)}.html")
        archive.setdefault(a["id"], a)
    cutoff = now_utc() - timedelta(days=ARCHIVE_DAYS)
    stories = sorted((a for a in archive.values() if parse_iso(a["created"]) >= cutoff),
                     key=lambda a: a["created"], reverse=True)
    save(ARCHIVE_FILE, stories)

    os.makedirs(PAGE_DIR, exist_ok=True)
    keep = set()
    for a in stories:
        sec_f = (lambda x: x["category"] == "sports") if a["category"] == "sports" else \
            (lambda x, r=a["region"]: x["region"] == r and x["category"] == "news")
        related = [r for r in stories if r["id"] != a["id"] and sec_f(r)][:6]
        name = os.path.basename(a["url"])
        keep.add(name)
        with open(os.path.join(PAGE_DIR, name), "w", encoding="utf-8") as f:
            f.write(story_page(a, related))
    for name in os.listdir(PAGE_DIR):
        if name.endswith(".html") and name not in keep:
            os.remove(os.path.join(PAGE_DIR, name))

    for key, (head, desc, test) in SECTIONS.items():
        items = [a for a in stories if test(a)][:80]
        body = f"<h1>{esc(head)}</h1><p class=\"by\">{esc(desc)}</p><ul class=\"list\">{list_items(items)}</ul>"
        with open(os.path.join(ROOT, f"{key}.html"), "w", encoding="utf-8") as f:
            f.write(shell(f"{head}: latest stories | {SITE_NAME}", desc, f"{SITE_URL}{key}.html", body))

    contact = CONFIG.get("contact_email", "").strip()
    about = f"""<h1>About {esc(SITE_NAME)}</h1>
<p>{esc(SITE_NAME)} brings Pakistan and world news together in one place. Live headlines from Pakistani and international channels update every hour, and each headline links to the full report on the original website.</p>
<h2>How our stories are written</h2>
<p>Twice a day, at about 8 AM and 8 PM Pakistan time, we publish short news stories written with AI assistance. Each story uses only facts that appear in the reports it lists as sources, names who reported each claim, and links to every source. Stories that contain a number not found in their sources are rejected automatically before publishing.</p>
<h2>Corrections</h2>
<p>If you find a mistake, please tell us{f' at <a href="mailto:{esc(contact)}">{esc(contact)}</a>' if contact else ''}. We correct errors as quickly as we can.</p>
<h2>Ownership of headlines</h2>
<p>Headlines, snippets and links belong to their original publishers. We show only titles, short snippets and links, never full copied articles.</p>"""
    with open(os.path.join(ROOT, "about.html"), "w", encoding="utf-8") as f:
        f.write(shell(f"About us and how we work | {SITE_NAME}", f"How {SITE_NAME} collects headlines and writes its news stories.",
                      f"{SITE_URL}about.html", about))

    today = now_utc().strftime("%Y-%m-%d")
    urls = [f"<url><loc>{SITE_URL}</loc><lastmod>{today}</lastmod><changefreq>hourly</changefreq></url>"]
    urls += [f"<url><loc>{SITE_URL}{k}.html</loc><lastmod>{today}</lastmod><changefreq>daily</changefreq></url>" for k in SECTIONS]
    urls += [f"<url><loc>{SITE_URL}about.html</loc></url>"]
    urls += [f"<url><loc>{SITE_URL}{a['url']}</loc><lastmod>{a['created'][:10]}</lastmod></url>" for a in stories]
    with open(os.path.join(ROOT, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                + "\n".join(urls) + "\n</urlset>\n")
    recent = [a for a in stories if now_utc() - parse_iso(a["created"]) <= timedelta(hours=48)]
    news_urls = "".join(
        f"<url><loc>{SITE_URL}{a['url']}</loc><news:news><news:publication><news:name>{esc(SITE_NAME)}</news:name>"
        f"<news:language>en</news:language></news:publication><news:publication_date>{a['created']}</news:publication_date>"
        f"<news:title>{esc(a['title'])}</news:title></news:news></url>\n" for a in recent)
    with open(os.path.join(ROOT, "news-sitemap.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
                'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">\n' + news_urls + "</urlset>\n")
    with open(os.path.join(ROOT, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}sitemap.xml\nSitemap: {SITE_URL}news-sitemap.xml\n")

    path = os.path.join(ROOT, "index.html")
    with open(path, encoding="utf-8") as f:
        page = f.read()
    tagline = CONFIG.get("tagline", "")
    desc = f"{tagline}. Live headlines from Pakistani and international channels every hour, plus our own news stories twice a day."
    page = re.sub(r"<title>.*?</title>", f"<title>{esc(SITE_NAME)}: Pakistan and World News Today</title>", page, count=1, flags=re.S)
    page = re.sub(r'(<h1 class="brand" id="brand">).*?(</h1>)', lambda m: m.group(1) + esc(SITE_NAME) + m.group(2), page, count=1)
    head = (f'<!--seo--><meta name="description" content="{esc(desc)}">\n<link rel="canonical" href="{SITE_URL}">\n'
            f'<meta name="robots" content="index, follow, max-image-preview:large">\n'
            f'<meta property="og:site_name" content="{esc(SITE_NAME)}"><meta property="og:title" content="{esc(SITE_NAME)}">'
            f'<meta property="og:description" content="{esc(desc)}"><meta property="og:url" content="{SITE_URL}"><meta property="og:type" content="website">\n'
            '<script type="application/ld+json">' + json.dumps([
                {"@context": "https://schema.org", "@type": "WebSite", "name": SITE_NAME, "url": SITE_URL},
                {"@context": "https://schema.org", "@type": "NewsMediaOrganization", "name": SITE_NAME, "url": SITE_URL,
                 "publishingPrinciples": f"{SITE_URL}about.html"}], ensure_ascii=False) + "</script><!--/seo-->")
    page = re.sub(r'<meta name="description"[^>]*>\n?', "", page)
    page = re.sub(r'<link rel="canonical"[^>]*>\n?', "", page)
    if "<!--seo-->" in page:
        page = re.sub(r"<!--seo-->.*?<!--/seo-->", lambda m: head, page, count=1, flags=re.S)
    else:
        page = page.replace("<title>", head + "\n<title>", 1)
    links = "".join(f'<a href="{k}.html">{esc(v[0])}</a> · ' for k, v in SECTIONS.items()) + '<a href="about.html">About us</a>'
    block = ('<!--stories--><nav class="sec-note" aria-label="All our stories"><h2 class="sec">All our stories</h2>'
             f'<p>{links}</p><ul>' + "".join(f'<li><a href="{esc(a["url"])}">{esc(a["title"])}</a></li>' for a in stories[:40])
             + "</ul></nav><!--/stories-->")
    if "<!--stories-->" in page:
        page = re.sub(r"<!--stories-->.*?<!--/stories-->", lambda m: block, page, count=1, flags=re.S)
    else:
        page = page.replace("  <footer>", block + "\n\n  <footer>", 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"Built {len(stories)} story pages, 3 section pages, about page and sitemaps.")

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "headlines"
    if mode == "headlines":
        run_headlines()
    elif mode == "articles":
        run_articles()
    elif mode == "site":
        build_site(load(ARTICLES_FILE, []))
    else:
        sys.exit("Use: python news.py headlines | articles")
