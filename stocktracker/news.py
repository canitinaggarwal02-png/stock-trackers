"""Collect news from Indian and global feeds plus a Google News search per company,
then work out which listed shares each story is about."""
import calendar
import hashlib
import html
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import feedparser

from . import http, nlp
from .symbols import clean_name

log = logging.getLogger(__name__)

GLOBAL_FEEDS = [
    "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=NSE+OR+BSE+shares+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/market.xml",
    "https://www.financialexpress.com/market/feed/",
    "https://www.thehindubusinessline.com/markets/feeder/default.rss",
]
GNEWS_SEARCH = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
TOKEN = re.compile(r"[A-Za-z0-9&]+(?:\.[A-Za-z0-9&]+)*")
MAX_ALIAS_WORDS = 5


class Matcher:
    """Finds company mentions by looking up every 1..5 word phrase of the text."""

    def __init__(self, con):
        self.ci, self.cs = {}, {}
        for r in con.execute("SELECT symbol, aliases FROM symbols"):
            for a in (r["aliases"] or "").split("|"):
                a = a.strip()
                if not a:
                    continue
                toks = TOKEN.findall(a)
                if not toks:
                    continue
                if len(toks) == 1 and a.isupper() and a.isalpha():
                    self.cs.setdefault(a, set()).add(r["symbol"])
                else:
                    self.ci.setdefault(" ".join(t.lower() for t in toks), set()).add(r["symbol"])

    def match(self, text):
        toks = TOKEN.findall(text or "")
        low = [t.lower() for t in toks]
        found = set()
        for i in range(len(toks)):
            if toks[i] in self.cs:
                found |= self.cs[toks[i]]
            for n in range(1, MAX_ALIAS_WORDS + 1):
                if i + n > len(toks):
                    break
                key = " ".join(low[i:i + n])
                if key in self.ci:
                    found |= self.ci[key]
        # A name shared by many companies (e.g. a group name) is not specific enough.
        return found if len(found) <= 6 else set()


def _strip_html(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _entry_time(e):
    st = e.get("published_parsed") or e.get("updated_parsed")
    if st:
        return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)
    return datetime.now(timezone.utc)


def fetch_feed(url):
    r = http.get(url, retries=2)
    feed = feedparser.parse(r.content)
    out = []
    for e in feed.entries:
        title = _strip_html(e.get("title"))
        if not title:
            continue
        src = (e.get("source") or {}).get("title") if isinstance(e.get("source"), dict) else None
        out.append(dict(title=title, summary=_strip_html(e.get("summary"))[:600],
                        link=e.get("link", ""), source=src or feed.feed.get("title", url),
                        published=_entry_time(e)))
    return out


def _store(con, item, matches, macro=None):
    nid = hashlib.sha1((item["link"] or item["title"]).encode()).hexdigest()[:16]
    con.execute("INSERT OR IGNORE INTO news VALUES (?,?,?,?,?,?,?)",
                (nid, item["title"], item["summary"], item["link"], item["source"],
                 item["published"].isoformat(), datetime.now(timezone.utc).isoformat()))
    cls = nlp.classify(item["title"], item["summary"])
    n = 0
    for sym in matches:
        con.execute("""INSERT OR IGNORE INTO news_symbols
            (news_id, symbol, event_type, sentiment, horizon, impact_score, explanation)
            VALUES (?,?,?,?,?,?,?)""",
                    (nid, sym, cls["event_type"], cls["sentiment"], cls["horizon"], cls["impact"],
                     cls["explanation"]))
        n += 1
    for sym, label, direction in (macro or []):
        con.execute("""INSERT OR IGNORE INTO news_symbols
            (news_id, symbol, event_type, sentiment, horizon, impact_score, explanation)
            VALUES (?,?,?,?,?,?,?)""",
                    (nid, sym, "macro", 0.5 * direction, "both", 0.4,
                     f"Global/macro theme '{label}': usually {'helps' if direction > 0 else 'hurts'} "
                     f"companies in this industry; effect can be short and long term."))
        n += 1
    return n


def _macro_targets(con, text, universe):
    hits = nlp.macro_hits(text)
    if not hits or not universe:
        return []
    out = []
    q = ",".join("?" * len(universe))
    rows = con.execute(f"SELECT symbol, LOWER(COALESCE(industry,'')) ind FROM symbols WHERE symbol IN ({q})",
                       list(universe)).fetchall()
    for label, inds, d in hits:
        for r in rows:
            if r["ind"] and any(k in r["ind"] for k in inds):
                out.append((r["symbol"], label, d))
    return out


def collect_news(con, cfg, focus_symbols, universe):
    """focus_symbols get a dedicated Google News search; universe gets macro mapping."""
    ncfg = cfg["news"]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ncfg["lookback_hours"])
    matcher = Matcher(con)
    stats = dict(feeds_ok=0, feeds_failed=[], items=0, links=0)

    def handle(items, must_include=None):
        for it in items:
            if it["published"] < cutoff:
                continue
            text = f"{it['title']} {it['summary']}"
            m = matcher.match(it["title"]) or matcher.match(text)
            if must_include and must_include not in m:
                continue
            macro = [] if m else _macro_targets(con, text, universe)
            if m or macro:
                stats["items"] += 1
                stats["links"] += _store(con, it, m, macro)

    for url in dict.fromkeys(GLOBAL_FEEDS + list(ncfg.get("extra_rss_feeds") or [])):
        try:
            handle(fetch_feed(url))
            stats["feeds_ok"] += 1
        except Exception as e:
            stats["feeds_failed"].append(f"{url}: {e}")

    if ncfg.get("google_news_per_company"):
        rows = con.execute(
            f"SELECT symbol, name FROM symbols WHERE symbol IN ({','.join('?' * len(focus_symbols))})",
            list(focus_symbols)).fetchall() if focus_symbols else []
        days = max(1, ncfg["lookback_hours"] // 24)
        for r in rows[: ncfg["google_news_company_limit"]]:
            q = quote_plus(f'"{clean_name(r["name"])}" when:{days}d')
            try:
                handle(fetch_feed(GNEWS_SEARCH.format(q=q)), must_include=r["symbol"])
                stats["feeds_ok"] += 1
            except Exception as e:
                stats["feeds_failed"].append(f"Google News {r['symbol']}: {e}")
    con.commit()
    return stats
