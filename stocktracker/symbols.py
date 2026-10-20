"""Daily refresh of every share listed on NSE and BSE, plus the search names
(aliases) used to spot each company in news text."""
import io
import logging
import re
from datetime import datetime

import pandas as pd

from . import http

log = logging.getLogger(__name__)

NSE_LIST = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
NSE_SME_LIST = "https://archives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv"
BSE_LIST = ("https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w?"
            "Group=&Scripcode=&industry=&segment=Equity&status=Active")

# Popular short names that the company name alone won't catch.
POPULAR_ALIASES = {
    "SBIN": ["SBI", "State Bank"], "LT": ["L&T", "Larsen"], "M&M": ["M&M", "Mahindra & Mahindra"],
    "HINDUNILVR": ["HUL", "Hindustan Unilever"], "BHARTIARTL": ["Airtel", "Bharti Airtel"],
    "MARUTI": ["Maruti", "Maruti Suzuki"], "TATAMOTORS": ["Tata Motors"], "TCS": ["TCS"],
    "INFY": ["Infosys"], "HDFCBANK": ["HDFC Bank"], "ICICIBANK": ["ICICI Bank"],
    "KOTAKBANK": ["Kotak Mahindra Bank", "Kotak Bank"], "AXISBANK": ["Axis Bank"],
    "RELIANCE": ["Reliance Industries", "RIL"], "ITC": ["ITC"], "ONGC": ["ONGC"],
    "NTPC": ["NTPC"], "BEL": ["Bharat Electronics"], "HAL": ["Hindustan Aeronautics", "HAL"],
    "BAJFINANCE": ["Bajaj Finance"], "BAJAJFINSV": ["Bajaj Finserv"], "ZOMATO": ["Zomato", "Eternal"],
    "ETERNAL": ["Zomato", "Eternal Ltd"], "PAYTM": ["Paytm", "One97"], "NYKAA": ["Nykaa"],
    "IRCTC": ["IRCTC"], "IDEA": ["Vodafone Idea", "Vi "], "ADANIENT": ["Adani Enterprises"],
    "ADANIPORTS": ["Adani Ports"], "SUNPHARMA": ["Sun Pharma"], "DRREDDY": ["Dr Reddy", "Dr. Reddy"],
    "ASIANPAINT": ["Asian Paints"], "TITAN": ["Titan Company"], "WIPRO": ["Wipro"],
    "HCLTECH": ["HCLTech", "HCL Tech"], "TECHM": ["Tech Mahindra"], "COALINDIA": ["Coal India"],
    "IOC": ["Indian Oil", "IOCL"], "BPCL": ["BPCL", "Bharat Petroleum"], "HINDALCO": ["Hindalco"],
    "TATASTEEL": ["Tata Steel"], "JSWSTEEL": ["JSW Steel"], "LICI": ["LIC"],
}

SUFFIXES = re.compile(r"\b(limited|ltd\.?|ltd|pvt|private|company|co\.|corporation|corp\.?|"
                      r"\(india\)|india|the|inc\.?|plc)\b", re.I)
GENERIC_FIRST = {"india", "indian", "national", "hindustan", "bharat", "tata", "bajaj", "adani",
                 "reliance", "jsw", "birla", "aditya", "mahindra", "godrej", "state", "the", "united",
                 "global", "new", "first", "shree", "shri", "sri", "jai", "royal", "star", "super",
                 "prime", "gold", "golden", "capital", "power", "energy", "steel", "bank", "finance",
                 "international", "gujarat", "punjab", "kerala", "tamil", "andhra", "southern",
                 "eastern", "western", "northern", "central", "asian", "oriental", "modern", "standard",
                 "general", "premier", "supreme", "jindal", "kotak", "hdfc", "icici", "axis", "sun",
                 "hero", "max", "future", "apollo", "vedanta", "indo", "ultra", "south", "east", "west",
                 "north", "great", "orient", "allied", "associated", "pioneer", "classic", "vijay",
                 "sharda", "laxmi", "lakshmi", "ganesh", "krishna", "balaji", "sai", "om", "maharashtra",
                 "rajasthan", "bengal", "delhi", "mumbai", "bombay", "madras", "calcutta", "kolkata"}
# Symbols that are ordinary English words would create false matches.
WORD_SYMBOLS = {"GOOD", "BEST", "MAX", "ONE", "SUN", "STAR", "HERO", "ACE", "GOLD", "CASH", "LOVE",
                "EASY", "TIME", "PLAY", "SAFE", "FIRST", "IDEA", "BASE", "CORE", "OPEN", "FACT",
                "TRUST", "ZEN", "MOIL", "GALAXY", "SHARE", "ELECT", "POWER", "INDIA", "ALL", "NEW",
                "NOW", "TOP", "BIG", "FUTURE", "PRIME", "ORIENT", "GLOBAL", "UNITED", "ROYAL"}


def clean_name(name):
    n = SUFFIXES.sub(" ", name or "")
    n = re.sub(r"[^A-Za-z0-9&\. ]", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def build_aliases(symbol, name, first_word_counts):
    aliases = set(POPULAR_ALIASES.get(symbol, []))
    cn = clean_name(name)
    if len(cn) >= 4:
        aliases.add(cn)
    words = cn.split()
    if words:
        fw = words[0]
        if (len(fw) >= 5 and fw.lower() not in GENERIC_FIRST
                and first_word_counts.get(fw.lower(), 0) == 1):
            aliases.add(fw)
    if len(symbol) >= 4 and symbol.isalpha() and symbol not in WORD_SYMBOLS:
        aliases.add(symbol)          # matched case-sensitively (see news.match)
    return "|".join(sorted(aliases))


def fetch_nse():
    frames = []
    for url, series in ((NSE_LIST, None), (NSE_SME_LIST, "SM")):
        try:
            df = pd.read_csv(io.StringIO(http.get(url).text))
            df.columns = [c.strip().upper() for c in df.columns]
            df = df.rename(columns={"NAME OF COMPANY": "name", "SYMBOL": "symbol",
                                    "ISIN NUMBER": "isin", "SERIES": "series"})
            if "series" not in df:
                df["series"] = series
            frames.append(df[["symbol", "name", "isin", "series"]])
        except Exception as e:
            log.warning("NSE list %s failed: %s", url, e)
    if not frames:
        return pd.DataFrame(columns=["symbol", "name", "isin", "series"])
    out = pd.concat(frames)
    out["exchange"] = "NSE"
    return out


def fetch_bse():
    r = http.get(BSE_LIST, headers={"Referer": "https://www.bseindia.com/",
                                    "Origin": "https://www.bseindia.com"})
    df = pd.DataFrame(r.json())
    df = df.rename(columns={"SCRIP_CD": "bse_code", "Scrip_Name": "name", "ISIN_NUMBER": "isin",
                            "INDUSTRY": "industry", "scrip_id": "bse_symbol"})
    return df[["bse_code", "name", "isin", "industry", "bse_symbol"]]


def refresh_symbols(con):
    """Download today's NSE and BSE lists and merge them on ISIN."""
    nse = fetch_nse()
    try:
        bse = fetch_bse()
    except Exception as e:
        log.warning("BSE list failed: %s", e)
        bse = pd.DataFrame(columns=["bse_code", "name", "isin", "industry", "bse_symbol"])
    if nse.empty and bse.empty:
        raise RuntimeError("Could not download NSE or BSE share lists")

    bse_by_isin = {r.isin: r for r in bse.itertuples() if isinstance(r.isin, str)}
    rows = []
    seen_isin = set()
    for r in nse.itertuples():
        b = bse_by_isin.get(r.isin)
        rows.append(dict(symbol=r.symbol, name=r.name, exchange="NSE" + ("+BSE" if b else ""),
                         isin=r.isin, bse_code=str(b.bse_code) if b else None, series=r.series,
                         industry=(b.industry if b is not None else None)))
        seen_isin.add(r.isin)
    for b in bse.itertuples():
        if b.isin in seen_isin:
            continue
        rows.append(dict(symbol=str(b.bse_code), name=b.name, exchange="BSE", isin=b.isin,
                         bse_code=str(b.bse_code), series=None, industry=b.industry))

    counts = {}
    for r in rows:
        w = clean_name(r["name"]).split()
        if w:
            counts[w[0].lower()] = counts.get(w[0].lower(), 0) + 1
    now = datetime.now().isoformat(timespec="seconds")
    for r in rows:
        r["aliases"] = build_aliases(r["symbol"], r["name"], counts)
        r["updated_at"] = now
        con.execute("""INSERT INTO symbols (symbol,name,exchange,isin,bse_code,series,industry,aliases,updated_at)
            VALUES (:symbol,:name,:exchange,:isin,:bse_code,:series,:industry,:aliases,:updated_at)
            ON CONFLICT(symbol) DO UPDATE SET name=excluded.name, exchange=excluded.exchange,
            isin=excluded.isin, bse_code=excluded.bse_code, series=excluded.series,
            industry=COALESCE(excluded.industry, symbols.industry), aliases=excluded.aliases,
            updated_at=excluded.updated_at""", r)
    con.commit()
    return len(rows)


def yahoo_ticker(row):
    """Yahoo Finance ticker for a symbols row."""
    if row["exchange"].startswith("NSE"):
        return row["symbol"] + ".NS"
    return f"{row['bse_code']}.BO"
