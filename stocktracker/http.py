"""HTTP helpers. NSE/BSE block requests that don't look like a browser, so we
send browser headers and, for NSE's JSON API, first visit the home page to get cookies."""
import logging
import time

import requests

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9", "Accept": "*/*"}

_session = None
_nse_ready = 0.0


def session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(HEADERS)
    return _session


def get(url, retries=3, timeout=25, **kw):
    last = None
    for i in range(retries):
        try:
            r = session().get(url, timeout=timeout, **kw)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
            if r.status_code in (401, 403) and "nseindia.com" in url:
                nse_warmup(force=True)
        except requests.RequestException as e:
            last = str(e)
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"GET {url} failed: {last}")


def nse_warmup(force=False):
    global _nse_ready
    if not force and time.time() - _nse_ready < 300:
        return
    try:
        session().get("https://www.nseindia.com/", timeout=20)
        session().get("https://www.nseindia.com/market-data/live-equity-market", timeout=20)
        _nse_ready = time.time()
    except requests.RequestException as e:
        log.warning("NSE warm-up failed: %s", e)


def nse_api(path):
    nse_warmup()
    r = get("https://www.nseindia.com/api/" + path.lstrip("/"),
            headers={"Referer": "https://www.nseindia.com/", "Accept": "application/json"})
    return r.json()
