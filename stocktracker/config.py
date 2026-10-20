import copy
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "db_path": "data/tracker.db",
    "deep_analysis_limit": 150,
    "schedule": {"full_update": "07:30", "intraday_news_every_min": 60, "after_market": "18:30",
                 "auto_in_dashboard": True},
    "news": {"google_news_per_company": True, "google_news_company_limit": 80,
             "lookback_hours": 48, "extra_rss_feeds": []},
    "big_investors": [],
    "alerts": {"stop_loss_pct": 8, "target_pct": 20, "volume_spike_ratio": 2.5,
               "delivery_spike_ratio": 1.8, "big_move_pct": 4, "min_hours_between_same_alert": 20},
    "dashboard": {"password": ""},
    "notifications": {"desktop": True, "telegram": {"enabled": False}, "email": {"enabled": False}},
}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path=None):
    path = Path(path or os.environ.get("STOCKTRACKER_CONFIG") or ROOT / "config.yaml")
    if not path.exists():
        path = ROOT / "config.example.yaml"
    with open(path, encoding="utf-8") as f:
        cfg = _merge(DEFAULTS, yaml.safe_load(f) or {})
    # Settings from environment variables (used when it runs on GitHub's servers)
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        cfg["notifications"]["telegram"] = dict(enabled=True, bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
                                                chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""))
    if os.environ.get("DEEP_ANALYSIS_LIMIT"):
        cfg["deep_analysis_limit"] = int(os.environ["DEEP_ANALYSIS_LIMIT"])
    if os.environ.get("CI"):
        cfg["notifications"]["desktop"] = False
    db = Path(cfg["db_path"])
    cfg["db_path"] = str(db if db.is_absolute() else ROOT / db)
    return cfg
