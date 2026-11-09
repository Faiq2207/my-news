#!/usr/bin/env python3
"""
News bot for your website.

  python news.py headlines   -> fetch latest headlines from all channels (runs every 30 min)
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
from urllib.parse import quote_plus

import feedparser
import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
HEADLINES_FILE = os.path.join(DATA, "headlines.json")
ARTICLES_FILE = os.path.join(DATA, "articles.json")
STATUS_FILE = os.path.join(DATA, "status.json")

with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
    CONFIG = json.load(f)

KEEP_DAYS = int(CONFIG.get("keep_days", 3))
N_NEWS = int(CONFIG.get("articles_news_per_section", 8))
N_SPORTS = int(CONFIG.get("articles_sports_per_section", 2))
SITE_NAME = CONFIG.get("site_name", "My News Channel")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
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
    {"name": "BBC News", "region": "international", "domain": "bbc.com",
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
Below are numbered news items collected in the last few hours from other news websites ({scope}).
Items starting with N are general news. Items starting with S are sports.

TASK
Pick the {n_news} most important DIFFERENT news stories (from N items) and the {n_sports} most important DIFFERENT sports stories (from S items).
Prefer stories reported by several sources. Write one short original article for each.

STRICT RULES
1. Use ONLY facts that appear in the items below. Never add facts, numbers, names, dates, quotes or background from your own knowledge.
2. Write every number with digits, exactly as it appears in the items.
3. Say who reported each claim, for example "according to Dawn" or "BBC reported".
4. If sources disagree, give both versions and name who reported each.
5. Write in your own words in English. Never copy a full sentence from an item.
6. Neutral tone. No opinions, guesses or predictions.
7. 100 to 200 words per article, in 2 to 4 short paragraphs.
8. "sources" lists the IDs of every item you used. At least one; two or more when possible.

Return only JSON in this shape:
{{"articles": [{{"category": "news or sports", "title": "...", "summary": "one sentence",
  "paragraphs": ["...", "..."], "sources": ["N3", "N17"]}}]}}

ITEMS
{items}
"""

NUM = re.compile(r"\d[\d,.]*\d|\d")


def ask_gemini(prompt, key):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}}
    for attempt in range(5):
        r = requests.post(url, json=body, timeout=180,
                          headers={"x-goog-api-key": key, "Content-Type": "application/json"})
        if r.status_code in (429, 500, 502, 503):
            wait = 30 * (attempt + 1)
            print(f"  AI busy ({r.status_code}), waiting {wait}s…")
            time.sleep(wait)
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"AI error {r.status_code}: {r.text[:300]}")
        parts = r.json()["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
        text = re.sub(r"^```(?:json)?|```$", "", text).strip()
        return json.loads(text)
    raise RuntimeError("AI service stayed busy, try again later")


def numbers_ok(article_text, ref_items):
    """Every number in the article must appear in at least one referenced item."""
    ref_text = " ".join(f"{h['title']} {h['summary']} {h['published']}" for h in ref_items)
    ref_nums = {n.replace(",", "").rstrip(".") for n in NUM.findall(ref_text)}
    missing = [n for n in NUM.findall(article_text)
               if n.replace(",", "").rstrip(".") not in ref_nums]
    return missing


def write_section(region, key, headlines):
    now = now_utc()
    recent = [h for h in headlines if h["region"] == region
              and now - parse_iso(h["published"]) <= timedelta(hours=12)]
    if len(recent) < 40:
        recent = [h for h in headlines if h["region"] == region
                  and now - parse_iso(h["published"]) <= timedelta(hours=24)]
    news = [h for h in recent if h["category"] == "news"][:160]
    sports = [h for h in recent if h["category"] == "sports"][:60]
    if not news:
        print(f"  No {region} headlines yet, skipping.")
        return []

    ref = {}
    lines = []
    for prefix, group in (("N", news), ("S", sports)):
        for i, h in enumerate(group, 1):
            rid = f"{prefix}{i}"
            ref[rid] = h
            text = f"[{rid}] ({h['source']}, {h['published'][:16].replace('T', ' ')} UTC) {h['title']}"
            if h["summary"]:
                text += f" | {h['summary']}"
            lines.append(text)

    scope = "Pakistan" if region == "local" else "international"
    prompt = PROMPT.format(site=SITE_NAME, scope=scope, n_news=N_NEWS,
                           n_sports=N_SPORTS if sports else 0, items="\n".join(lines))
    result = ask_gemini(prompt, key)

    out, counts = [], {"news": 0, "sports": 0}
    for a in result.get("articles", []):
        cat = "sports" if a.get("category") == "sports" else "news"
        limit = N_SPORTS if cat == "sports" else N_NEWS
        refs = [ref[r] for r in a.get("sources", []) if r in ref]
        paras = [p.strip() for p in a.get("paragraphs", []) if p and p.strip()]
        title = (a.get("title") or "").strip()
        if not refs or not paras or not title or counts[cat] >= limit:
            continue
        missing = numbers_ok(" ".join([title, a.get("summary", "")] + paras), refs)
        if missing:
            print(f"  Rejected (numbers not found in sources {missing}): {title[:70]}")
            continue
        counts[cat] += 1
        out.append({
            "id": hashlib.sha1(f"{now.isoformat()}{title}".encode()).hexdigest()[:16],
            "region": region,
            "category": cat,
            "title": title,
            "summary": (a.get("summary") or "").strip(),
            "paragraphs": paras,
            "sources": [{"source": h["source"], "title": h["title"], "link": h["link"],
                         "published": h["published"], "used_text": h["summary"]} for h in refs],
            "created": iso(now),
        })
    print(f"  {region}: {counts['news']} news + {counts['sports']} sports articles kept")
    return out


def run_articles():
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        sys.exit("GEMINI_API_KEY is missing. Add it under Settings > Secrets and variables > Actions.")
    headlines = load(HEADLINES_FILE, [])
    new = []
    for region in ("local", "international"):
        print(f"Writing {region} articles…")
        try:
            new += write_section(region, key, headlines)
        except Exception as ex:
            print(f"  Could not write {region} articles: {ex}")
    old = load(ARTICLES_FILE, [])
    articles = prune(new + old, "created")
    articles.sort(key=lambda a: a["created"], reverse=True)
    save(ARTICLES_FILE, articles)
    print(f"Saved {len(articles)} articles (last {KEEP_DAYS} days).")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "headlines"
    if mode == "headlines":
        run_headlines()
    elif mode == "articles":
        run_articles()
    else:
        sys.exit("Use: python news.py headlines | articles")
