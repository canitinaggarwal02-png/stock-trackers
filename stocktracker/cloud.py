"""Running the tracker on GitHub's free servers once a day, for use from a phone.
Your shares come from the MY_SHARES secret (or my_shares.csv). The result is a web page
(GitHub Pages), locked with your password, plus Telegram messages if you set a bot up."""
import base64
import csv
import html
import io
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from . import analysis, db, notify, pipeline, portfolio, report, sources, textviews  # noqa: F401

log = logging.getLogger(__name__)
STATUS = {"bought": "bought", "holding": "bought", "sold": "sold", "short": "short", "short sold": "short",
          "want to buy": "want_buy", "want_buy": "want_buy", "buy": "want_buy",
          "want to sell": "want_sell", "want_sell": "want_sell", "sell": "want_sell"}


def _num(x):
    try:
        return float(str(x).replace(",", "").replace("₹", "").strip()) if str(x).strip() else None
    except ValueError:
        return None


def sync_shares(con, path):
    """Make the tracked shares match the file: new rows are added, changed rows updated,
    rows removed from the file stop being tracked. Returns a list of messages."""
    msgs, keep = [], set()
    text = os.environ.get("MY_SHARES", "").strip()
    if text:
        if not text.lower().startswith("share"):
            text = "share,status,qty,price,target,stop_loss,note\n" + text
        rows = list(csv.DictReader(io.StringIO(text)))
    else:
        try:
            rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
        except FileNotFoundError:
            return ["No shares listed yet (MY_SHARES secret is empty)"]
    cur = {(r["symbol"], r["kind"]): r["id"] for r in portfolio.active(con).to_dict("records")}
    for r in rows:
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        if not r.get("share"):
            continue
        kind = STATUS.get(r.get("status", "").lower())
        if not kind:
            msgs.append(f"{r['share']}: status '{r.get('status')}' not understood (use bought, sold, short, want to buy, want to sell)")
            continue
        try:
            sym = portfolio.resolve(con, r["share"])
        except ValueError as e:
            msgs.append(str(e))
            continue
        vals = (_num(r.get("qty")), _num(r.get("price")), _num(r.get("target")), _num(r.get("stop_loss")), r.get("note", ""))
        if (sym, kind) in cur:
            pid = cur[(sym, kind)]
            con.execute("UPDATE positions SET qty=?, price=?, target=?, stop_loss=?, note=? WHERE id=?", (*vals, pid))
        else:
            pid, _ = portfolio.add(con, sym, kind, *vals)
            msgs.append(f"Now tracking {sym} ({portfolio.KINDS[kind]})")
        keep.add(pid)
    for pid in cur.values():
        if pid not in keep:
            portfolio.remove(con, pid)
    con.commit()
    return msgs


def daily(cfg, shares_csv):
    con = db.connect(cfg["db_path"])
    if not con.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]:
        from . import symbols
        from datetime import date
        symbols.refresh_symbols(con)              # share list is needed to read my_shares.csv
        db.set_meta(con, "symbols_date", date.today().isoformat())
    notes = sync_shares(con, shares_csv)
    con.close()
    steps = pipeline.full_update(cfg)
    con = db.connect(cfg["db_path"])
    path = report.write_recommended_html(con, cfg)
    write_site(con, cfg, steps, notes, os.environ.get("SITE_DIR", "site"), os.environ.get("SITE_PASSWORD", ""))
    tg = cfg["notifications"].get("telegram") or {}
    if tg.get("enabled"):
        failed = [s["step"] for s in steps if not s["ok"]]
        text = textviews.picks(con, 5)
        text += "\n\n" + " · ".join(analysis.track_text(analysis.track_record(con)))
        if failed:
            text += "\n\n⚠️ Could not update: " + ", ".join(failed)
        if notes:
            text += "\n\n" + "\n".join(notes)
        notify.telegram(tg, f"Daily update {datetime.now():%d %b}", text)
        notify.telegram_file(tg, path, "Recommended shares with full reports and sources. Open it in Chrome.")
    con.close()
    return steps


# ---------- the web page ----------

def my_shares_html(con, steps, notes):
    """'My shares and reminders' block for the top of the page."""
    from .prices import history
    b = ["<h2 id='mine'>💼 My shares and reminders</h2>"]
    failed = [s["step"] for s in steps if not s["ok"]]
    b.append(f"<p class='muted'>Updated {datetime.now(report.IST):%d %b %Y %H:%M} IST. "
             + ("All data sources worked." if not failed else "Could not update: " + html.escape(", ".join(failed)) + ".")
             + "</p>")
    b += [f"<p class='badge {'neutral' if n.startswith('Now tracking') else 'bad'}'>{html.escape(n)}</p>" for n in notes]
    pos = portfolio.active(con)
    if pos.empty:
        b.append("<p class='muted'>No shares listed. Add them to the MY_SHARES secret on GitHub.</p>")
    else:
        rows = ["<tr><th>Share</th><th>Status</th><th>My price</th><th>Last</th><th>P&amp;L</th></tr>"]
        import pandas as pd
        for p in pos.itertuples():
            price = p.price if pd.notna(p.price) else None
            h = history(con, p.symbol, 2)
            last = float(h.close.iloc[-1]) if not h.empty else None
            pnl = (last / price - 1) * 100 * (-1 if p.kind == "short" else 1) if last and price and p.kind in ("bought", "short") else None
            rows.append(f"<tr><td><a href='#r-{html.escape(p.symbol)}'>{html.escape(p.symbol)}</a></td>"
                        f"<td>{html.escape(portfolio.KINDS[p.kind])}</td><td>{'' if not price else f'₹{price:,.2f}'}</td>"
                        f"<td>{'–' if last is None else f'₹{last:,.2f}'}</td><td>{'' if pnl is None else f'{pnl:+.1f}%'}</td></tr>")
        b.append("<div class='tw'><table>" + "".join(rows) + "</table></div>")
    since = (datetime.now() - timedelta(days=7)).isoformat()
    al = con.execute("SELECT * FROM alerts WHERE created_at>=? ORDER BY id DESC LIMIT 40", (since,)).fetchall()
    b.append("<h3>🔔 Reminders (last 7 days)</h3>" if al else "<p class='muted'>No reminders in the last 7 days.</p>")
    for a in al:
        items = "".join(f"<li>{sources.to_html(ln[2:] if ln.startswith('• ') else ln)}</li>"
                        for ln in (a["body"] or "").splitlines() if ln.strip())
        b.append(f"<div class='pick'><b>{html.escape(a['title'])}</b> <small>{a['created_at'][:16].replace('T', ' ')}</small><ul>{items}</ul></div>")
    return "".join(b)


def write_site(con, cfg, steps, notes, out_dir, password=""):
    page = report.recommended_html(con, cfg, top=my_shares_html(con, steps, notes),
                                   extra_syms=portfolio.tracked_symbols(con))
    page = page.replace("<title>Recommended shares</title>",
                        "<title>Stock Tracker</title><meta name='robots' content='noindex'>")
    if password:
        page = lock(page, password)
    else:      # the page address is public: never publish your shares unlocked
        page = LOCK_PAGE.split("<form")[0] + ("<p style='padding:20px'>Add a <b>SITE_PASSWORD</b> secret on GitHub "
                                               "(Settings → Secrets and variables → Actions), then run the workflow again.</p></body></html>")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(page, encoding="utf-8")
    return str(out / "index.html")


def lock(page, password):
    """Encrypt the page (AES-GCM, key from the password with PBKDF2) so only someone with the
    password can read it, even though the address is public."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=250_000).derive(password.encode())
    data = AESGCM(key).encrypt(iv, page.encode(), None)
    blob = json.dumps({k: base64.b64encode(v).decode() for k, v in (("s", salt), ("i", iv), ("d", data))})
    return LOCK_PAGE.replace("__BLOB__", blob)


LOCK_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>Stock Tracker</title><style>
body{margin:0;font:17px/1.5 system-ui,sans-serif;background:#f6f8fa;color:#1f2328;display:flex;min-height:100vh;align-items:center;justify-content:center}
@media (prefers-color-scheme:dark){body{background:#0f1115;color:#e6e8eb}input{background:#171a20;color:#e6e8eb}}
form{width:min(340px,90vw);text-align:center}input,button{width:100%;box-sizing:border-box;font-size:17px;padding:12px;border-radius:10px;border:1px solid #9aa4b2;margin:6px 0}
button{background:#0b63c4;color:#fff;border:0}#e{color:#c62828;min-height:1.5em}label{display:flex;gap:8px;align-items:center;justify-content:center;font-size:15px}
label input{width:auto;margin:0}</style></head><body>
<form id="f"><h2>📈 Stock Tracker</h2><input id="p" type="password" placeholder="Password" autocomplete="current-password" autofocus>
<label><input type="checkbox" id="r" checked> Remember on this phone</label><button>Open</button><div id="e"></div></form>
<script>
const B=__BLOB__;const u=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
async function open_(pw){const k0=await crypto.subtle.importKey("raw",new TextEncoder().encode(pw),"PBKDF2",false,["deriveKey"]);
const k=await crypto.subtle.deriveKey({name:"PBKDF2",salt:u(B.s),iterations:250000,hash:"SHA-256"},k0,{name:"AES-GCM",length:256},false,["decrypt"]);
const t=new TextDecoder().decode(await crypto.subtle.decrypt({name:"AES-GCM",iv:u(B.i)},k,u(B.d)));document.open();document.write(t);document.close();}
async function go(pw,save){try{await open_(pw);if(save)try{localStorage.setItem("stpw",pw)}catch(e){}}
catch(e){document.getElementById("e").textContent="Wrong password";try{localStorage.removeItem("stpw")}catch(e){}}}
document.getElementById("f").onsubmit=e=>{e.preventDefault();go(document.getElementById("p").value,document.getElementById("r").checked)};
try{const s=localStorage.getItem("stpw");if(s)go(s,true)}catch(e){}
</script></body></html>"""
