"""Send alerts to the desktop, Telegram and/or e-mail."""
import logging
import smtplib
from email.message import EmailMessage

import requests

from . import DISCLAIMER

log = logging.getLogger(__name__)


def desktop(title, body):
    import re
    body = re.sub(r", https?://[^\]]+\]", "]", body)     # web addresses don't fit a pop-up
    try:
        from plyer import notification
        notification.notify(title=title[:63], message=body[:250], app_name="Stock Tracker", timeout=15)
        return True
    except Exception as e:
        log.info("desktop notification unavailable (%s); printing instead", e)
        print(f"\n🔔 {title}\n{body}\n")
        return False


def chat_id(cfg):
    """The chat to write to; if not set, the last person who messaged the bot (send it /start once)."""
    if cfg.get("chat_id"):
        return cfg["chat_id"]
    r = requests.get(f"https://api.telegram.org/bot{cfg['bot_token']}/getUpdates", timeout=20).json()
    for u in reversed(r.get("result", [])):
        m = u.get("message") or u.get("edited_message")
        if m:
            cfg["chat_id"] = str(m["chat"]["id"])
            return cfg["chat_id"]
    raise RuntimeError("no chat found: open your bot in Telegram and send it /start")


def telegram(cfg, title, body):
    url = f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage"
    text = f"🔔 {title}\n\n{body}\n\n{DISCLAIMER}"
    for i in range(0, len(text), 4000):                 # Telegram's limit is 4096 characters
        r = requests.post(url, json={"chat_id": chat_id(cfg), "text": text[i:i + 4000],
                                     "disable_web_page_preview": True}, timeout=20)
        r.raise_for_status()


def telegram_file(cfg, path, caption=""):
    with open(path, "rb") as f:
        r = requests.post(f"https://api.telegram.org/bot{cfg['bot_token']}/sendDocument",
                          data={"chat_id": chat_id(cfg), "caption": caption[:1000]}, files={"document": f}, timeout=60)
    r.raise_for_status()


def email(cfg, title, body):
    msg = EmailMessage()
    msg["Subject"] = f"[Stock Tracker] {title}"
    msg["From"] = cfg["username"]
    msg["To"] = cfg["to"]
    msg.set_content(f"{body}\n\n{DISCLAIMER}")
    with smtplib.SMTP(cfg["smtp_host"], int(cfg["smtp_port"]), timeout=30) as s:
        s.starttls()
        s.login(cfg["username"], cfg["password"])
        s.send_message(msg)


def send(ncfg, title, body):
    ok = False
    if ncfg.get("desktop", True):
        desktop(title, body)
        ok = True
    for name, fn in (("telegram", telegram), ("email", email)):
        c = ncfg.get(name) or {}
        if c.get("enabled"):
            try:
                fn(c, title, body)
                ok = True
            except Exception as e:
                log.warning("%s notification failed: %s", name, e)
    return ok
