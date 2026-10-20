#!/usr/bin/env python3
"""Indian Stock News & Impact Tracker — command line.

  python run.py update            full daily update (share list, news, deals, analysis, reminders)
  python run.py quick             news + reminders only (fast, for during the day)
  python run.py dashboard         open the dashboard in your browser
  python run.py dashboard --phone also open it to phones on your home Wi-Fi
  python run.py bot               Telegram bot only (use the tracker from your phone)
  python run.py scheduler         keep running and update automatically every day
  python run.py add INFY bought --qty 10 --price 1500 [--target 1800 --stop 1380]
  python run.py add "Tata Motors" want_buy --target 900
  python run.py list              your shares and watchlist
  python run.py remove 3          stop tracking entry #3
  python run.py alerts            check reminders now
  python run.py picks             short-term and long-term ideas
  python run.py html              save recommended shares + full reports as one HTML file
  python run.py demo              build a demo database with made-up data (no internet needed)
  python run.py cloud my_shares.csv   daily run on GitHub's servers (see PHONE_SETUP.md)
"""
import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from stocktracker import DISCLAIMER, alerts, analysis, db, pipeline, portfolio  # noqa: E402
from stocktracker.config import ROOT, load_config  # noqa: E402


def print_steps(steps):
    for s in steps:
        print(f"  {'✓' if s['ok'] else '✗'} {s['step']} ({s['secs']}s): {s['result'][:160]}")


def lan_ip():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "YOUR-COMPUTER-IP"
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    ap.add_argument("--demo", action="store_true", help="use the demo database")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("update")
    sub.add_parser("quick")
    dp = sub.add_parser("dashboard")
    dp.add_argument("--phone", action="store_true", help="allow phones on the same Wi-Fi to open it")
    sub.add_parser("bot")
    sub.add_parser("scheduler")
    sub.add_parser("list")
    sub.add_parser("alerts")
    sub.add_parser("picks")
    hp = sub.add_parser("html")
    hp.add_argument("--out", help="where to save (default data/recommended.html)")
    sub.add_parser("demo")
    cp = sub.add_parser("cloud")
    cp.add_argument("shares_csv", help="your shares file (my_shares.csv)")
    a = sub.add_parser("add")
    a.add_argument("share", help="NSE symbol, BSE code or company name")
    a.add_argument("kind", choices=list(portfolio.KINDS))
    a.add_argument("--qty", type=float)
    a.add_argument("--price", type=float, help="price you bought/sold at")
    a.add_argument("--target", type=float, help="target price (or the price you want to buy/sell at)")
    a.add_argument("--stop", type=float, help="stop loss")
    a.add_argument("--note", default="")
    r = sub.add_parser("remove")
    r.add_argument("id", type=int)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    cfg = load_config(args.config)
    if args.demo:
        cfg["db_path"] = cfg["db_path"].replace(".db", "_demo.db")

    if args.cmd == "update":
        print_steps(pipeline.full_update(cfg))
    elif args.cmd == "quick":
        print_steps(pipeline.quick_update(cfg))
    elif args.cmd == "cloud":
        from stocktracker import cloud
        print_steps(cloud.daily(cfg, args.shares_csv))
    elif args.cmd == "demo":
        from stocktracker import demo
        path, new = demo.build(cfg)
        print(f"Demo database written to {path} with {len(new)} sample reminders.")
        print("Open it with:  python run.py --demo dashboard")
    elif args.cmd == "dashboard":
        env = dict(os.environ, STOCKTRACKER_DB=cfg["db_path"])
        if args.config:
            env["STOCKTRACKER_CONFIG"] = args.config
        cmd = [sys.executable, "-m", "streamlit", "run", str(ROOT / "app.py"), "--client.toolbarMode", "minimal",
               "--browser.gatherUsageStats", "false"]
        if args.phone:
            if not cfg.get("dashboard", {}).get("password"):
                print("⚠️  Set dashboard → password in config.yaml first, so only you can open it.")
                return
            cmd += ["--server.address", "0.0.0.0"]
            print(f"\n📱 On your phone (same Wi-Fi) open:  http://{lan_ip()}:8501\n")
        subprocess.call(cmd, env=env)
    elif args.cmd == "bot":
        from stocktracker.bot import Bot
        Bot(cfg).run()
    elif args.cmd == "scheduler":
        from stocktracker.scheduler import run_forever
        run_forever(cfg)
    else:
        con = db.connect(cfg["db_path"])
        if args.cmd == "add":
            pid, sym = portfolio.add(con, args.share, args.kind, args.qty, args.price, args.target, args.stop, args.note)
            print(f"Now tracking #{pid}: {sym} ({portfolio.KINDS[args.kind]}). Reminders will come when something needs attention.")
        elif args.cmd == "remove":
            portfolio.remove(con, args.id)
            print(f"Stopped tracking #{args.id}.")
        elif args.cmd == "list":
            df = portfolio.active(con)
            print(df[["id", "symbol", "name", "kind", "qty", "price", "target", "stop_loss", "note"]].to_string(index=False)
                  if not df.empty else "Nothing tracked yet. Use: python run.py add SYMBOL bought --qty 10 --price 100")
        elif args.cmd == "alerts":
            new = alerts.run_alerts(con, cfg)
            print(f"{len(new)} new reminder(s).")
            for t, b in new:
                print(f"\n{t}\n{b}")
        elif args.cmd == "html":
            from stocktracker import report
            path = report.write_recommended_html(con, cfg, args.out, db.get_meta(con, "demo_mode") == "1")
            print(f"Saved {path}. Open it in any browser, or send it to your phone.")
        elif args.cmd == "picks":
            from stocktracker import sources
            short, long = analysis.picks(con)
            for label, df, col, rc in (("SHORT TERM", short, "short_score", "short_reasons"),
                                       ("LONG TERM", long, "long_score", "long_reasons")):
                print(f"\n=== {label} ideas ===")
                for r in df.itertuples():
                    print(f"{r.symbol:12} {getattr(r, col):6.1f}  {r.name}")
                    for x in getattr(r, rc)[:3]:
                        print(f"{'':14}- {sources.short(x)}")
        print(f"\n{DISCLAIMER}")
        con.close()


if __name__ == "__main__":
    main()
