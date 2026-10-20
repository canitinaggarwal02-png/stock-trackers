"""Where every piece of data came from. Reasons carry their source at the end as
"text [Source: name, date, url]" so it survives being stored, sent as a reminder
or shown on any screen."""
import html
import re

from . import db

SOURCES = {
    "delivery": ("NSE daily traded & delivery report", "https://www.nseindia.com/all-reports"),
    "deals": ("NSE bulk & block deals", "https://www.nseindia.com/report-detail/display-bulk-and-block-deals"),
    "fii": ("NSE FII/DII trading activity", "https://www.nseindia.com/reports/fii-dii"),
    "shp": ("NSE shareholding pattern",
            "https://www.nseindia.com/companies-listing/corporate-filings-shareholding-pattern"),
    "prices": ("Yahoo Finance price history", "https://finance.yahoo.com/quote/{t}/history"),
    "fund": ("Yahoo Finance financials", "https://finance.yahoo.com/quote/{t}/financials"),
    "symbols": ("NSE & BSE listed share lists", "https://www.nseindia.com/market-data/securities-available-for-trading"),
    "records": ("Tracker's own records", ""),
}
_TAG = re.compile(r"\s*\[Source: ([^\]]+)\]\s*$")


def _clean(s):
    return re.sub(r"[\[\],]", " ", str(s or "")).strip()


def note(con, key, url=None):
    """Remember when (and from which address) a dataset was last fetched."""
    from datetime import datetime
    db.set_meta(con, "src:" + key, f"{datetime.now().isoformat(timespec='minutes')}|{url or ''}")


def fetched(con, key):
    v = db.get_meta(con, "src:" + key)
    return v.split("|", 1)[0].replace("T", " ") if v else None


def url_for(key, ticker=None):
    u = SOURCES.get(key, ("", ""))[1]
    if "{t}" in u:
        return u.format(t=ticker) if ticker else "https://finance.yahoo.com"
    return u


def tag(key=None, date=None, name=None, url=None, ticker=None):
    if key:
        name = name or SOURCES[key][0]
        url = url or url_for(key, ticker)
    parts = [_clean(name)] + ([_clean(date)] if date else []) + ([url.replace(",", "%2C")] if url else [])
    return " [Source: " + ", ".join(parts) + "]"


def with_src(text, key=None, date=None, **kw):
    return text + tag(key, date, **kw)


def split(reason):
    """-> (text, name, date, url); missing parts are ''."""
    m = _TAG.search(reason or "")
    if not m:
        return reason, "", "", ""
    parts = [p.strip() for p in m.group(1).split(",")]
    url = parts.pop() if parts and parts[-1].startswith("http") else ""
    name = parts[0] if parts else ""
    date = ", ".join(parts[1:])
    return reason[:m.start()], name, date, url


def plain(reason):
    """Text only, for short lists."""
    return split(reason)[0]


def short(reason):
    """Text plus 'Source: name, date' without the web address (notifications)."""
    t, n, d, _ = split(reason)
    return t + (f" [Source: {n}{', ' + d if d else ''}]" if n else "")


def md(reason):
    """Markdown with the source as a link on a new line."""
    t, n, d, u = split(reason)
    if not n:
        return t
    s = f"[{n}]({u})" if u else n
    return f"{t}  \n  _Source: {s}{' · ' + d if d else ''}_"


def src_md(key, date=None, ticker=None, name=None, url=None):
    return md(with_src("", key, date, ticker=ticker, name=name, url=url)).strip()


def to_html(reason):
    t, n, d, u = split(reason)
    out = html.escape(t)
    if n:
        s = f"<a href='{html.escape(u)}' target='_blank'>{html.escape(n)}</a>" if u else html.escape(n)
        out += f" <span class='src'>Source: {s}{' · ' + html.escape(d) if d else ''}</span>"
    return out
