# Indian Stock News & Impact Tracker

A program that runs on your own computer and, every day:

1. **Downloads the full list of shares listed on NSE and BSE** (so it always knows which companies to look for).
2. **Collects news from India and around the world** (Economic Times, Moneycontrol, Mint, Business Standard,
   NDTV Profit, CNBC-TV18, Financial Express, BusinessLine, Google News India/world/business, plus a Google News search for each of your shares and the most
   traded shares) and works out **which listed company each story is about**.
3. **Reads each headline** to decide the type of event (management change, results, order win, product approval,
   fraud/regulator action, merger, dividend/bonus, promoter selling, fund raising, plant shutdown, global themes
   like crude oil, rupee, interest rates, tariffs…), whether it is **good or bad**, and whether the effect is
   **short term, long term or both**.
4. **Checks the price**: has the news **already moved the share** ("already in price") or **not yet** ("effect may
   still come"), compared with how much the share normally moves.
5. **Daily buying/selling activity** for every NSE share: traded quantity, delivery quantity (shares actually taken
   home), change vs previous day, and spikes vs the 20-day average.
6. **Bulk and block deals**, flagging **well-known investors** (Damani, Kacholia, Kedia, Dolly Khanna, Singhania,
   Goldman Sachs, LIC… the list is editable in `config.yaml`).
7. **FII and DII** daily buying/selling, and each share's **shareholding**: promoters, FII, DII, mutual funds, and
   change since last quarter, plus any well-known investors named in the shareholder list.
8. **Financial health brief** for each share (ROE, debt, margins, sales and profit growth, valuation) in plain words.
9. **Short-term ideas** and **long-term ideas**, separately, each with the reasons, and **the source of every
   reason** (publisher and link for news, the NSE report, Yahoo Finance page or the tracker's own records, with the date).
10. **Keeps records and learns from them**: everything collected stays in the database (news, years of prices,
    deals, quarterly shareholding, quarterly results, and every day's scores). The analysis compares today with the
    past: how similar news moved each price in the following 5 trading days, holdings over past quarters, the latest
    quarter vs a year ago, and a **track record** of the tracker's own past calls.
11. **Your shares**: tell it you *bought*, *sold*, *short-sold*, *want to buy* or *want to sell* a share and it keeps
    a special eye on it, sending **reminders with a recommendation and reasons** (stop loss hit, target reached,
    negative news, big investor selling, heavy selling volume, your buy price reached, good time to buy, wait…)
    by **desktop notification, Telegram or e-mail**.

> ⚠️ **Not financial advice.** Every signal comes from simple, transparent rules applied to free public data that
> can be late, incomplete or wrong. Use it to find things worth checking, then do your own research or talk to a
> SEBI-registered adviser before trading.

---

## 1. Install (one time)

You need **Python 3.10 or newer** ([python.org/downloads](https://www.python.org/downloads/); on Windows tick
"Add Python to PATH" during install).

Copy the `stock-tracker` folder to your computer, open a terminal (Windows: *Command Prompt*) in that folder and run:

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Mac / Linux:
source .venv/bin/activate

pip install -r requirements.txt
copy config.example.yaml config.yaml      # Mac/Linux: cp config.example.yaml config.yaml
```

## 2. Try it with demo data first (optional, works offline)

```bash
python run.py demo
python run.py --demo dashboard
```

Your browser opens the dashboard filled with **made-up** data so you can see every screen.

## 3. Real use

```bash
python run.py update        # first run takes 10–30 minutes (downloads ~35 days of history)
python run.py dashboard     # opens http://localhost:8501 in your browser
```

Tell it about your shares, either in the dashboard (**My shares & watchlist** page, or the form at the bottom of any
share's page) or from the terminal:

```bash
python run.py add INFY bought --qty 10 --price 1500 --stop 1380 --target 1800
python run.py add "Tata Motors" want_buy --target 900       # remind me when it reaches ₹900
python run.py add HDFCBANK want_sell --target 1900
python run.py add IRCTC sold --price 950                     # watch for a chance to buy back
python run.py list
python run.py remove 3
python run.py picks                                          # short and long term ideas in the terminal
python run.py alerts                                         # check reminders right now
```

### Keep it running automatically

It refreshes by itself in two ways:
- **While the dashboard is open**: it runs the daily refresh at `full_update` time (07:30 by default), or as soon as
  the dashboard starts if that time was missed, and checks news every hour on market days. The **↻ Refresh now**
  button in the side menu runs a full refresh whenever you want.
- **Without the dashboard**: run the scheduler below.

```bash
python run.py scheduler
```

Leave that window open. If today's refresh hasn't run yet it runs it straight away, then a full update at 07:30 and 18:30 (India time; exchange data for the day is
published in the evening) and checks news + reminders every hour from 9:00 to 16:00 on weekdays. Times are in
`config.yaml`.

To start it when your computer starts:
- **Windows**: Task Scheduler → *Create Basic Task* → trigger *When I log on* → action *Start a program*:
  `C:\path\to\stock-tracker\.venv\Scripts\python.exe` with arguments `run.py scheduler` and *Start in*
  `C:\path\to\stock-tracker`.
- **Mac/Linux**: add to crontab `@reboot cd /path/to/stock-tracker && .venv/bin/python run.py scheduler`.

### Notifications

In `config.yaml`:
- **Desktop** pop-ups are on by default.
- **Telegram** (works on your phone): message `@BotFather` → `/newbot` → copy the token. Send any message to your new
  bot, open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`. Put both in `config.yaml` and set
  `enabled: true`.
- **E-mail**: fill in SMTP details. For Gmail create an *App Password* (Google Account → Security → App passwords).

Use **Data & updates → Send a test notification** in the dashboard to check.

## 📱 Using it on your phone

**No computer?** See [PHONE_SETUP.md](PHONE_SETUP.md): it runs free on GitHub's servers every day and publishes the report and reminders as your own password-protected web page.

The tracker runs on your computer (that's where it collects data every day) and your phone talks to it.
There are two ways, and you can use both.

### A. Telegram bot (works anywhere: home, office, travelling)

1. In Telegram, open **@BotFather**, send `/newbot`, pick any name. Copy the **token** it gives you.
2. In `config.yaml` under `notifications → telegram` paste the token as `bot_token` and set `enabled: true`.
3. On the computer run `python run.py bot`, then open your new bot on your phone and send `/start`.
   It replies with your **chat id**. Paste that number as `chat_id` in `config.yaml`.
4. From now on just run `python run.py scheduler`: it starts the bot too, runs the daily updates and sends
   reminders to your phone. The bot only answers your own chat id.

What you can send from your phone:

```
/picks                     short & long term ideas
/recommended               today's recommended shares
/report INFY               full report: recommendation, reasons and all data
/html                      recommended shares + all reports as one HTML file
/share INFY                quick details (also works with names: /share tata motors)
/news   /news INFY         important news, good/bad, short/long term, already in price or not
/my                        your shares, watchlist and P&L
/fii   /bigdeals           FII/DII activity, deals by well-known investors
/bought INFY 10 1500 1380 1800    bought 10 at 1500 (optional stop 1380, target 1800)
/sold INFY 1650            sold at 1650 (watch for re-entry)
/short INFY 10 1500        short-sold 10 at 1500
/wantbuy INFY 1400         remind me when it's a good time / reaches 1400
/wantsell INFY 1800
/remove 3                  stop tracking #3
/alerts   /update          check reminders now / fetch fresh news now
```

### B. Full dashboard in your phone's browser (same Wi-Fi as the computer)

1. Set a password in `config.yaml`: `dashboard → password: "something-only-you-know"`.
2. Run `python run.py dashboard --phone`. It prints an address like `http://192.168.1.5:8501`.
3. Open that address in Chrome/Safari on your phone and enter the password. Use **Add to Home screen**
   in the browser menu so it opens like an app. Tap **»** at the top-left for the menu.

Windows may ask whether Python can use the network; allow it on **Private networks**.

To open the dashboard away from home, install the free **Tailscale** app on the computer and the phone
(sign in with the same account) and use the computer's Tailscale address instead. This keeps it private;
don't open the port to the whole internet.

The computer must be on (and not asleep) for the bot and dashboard to answer.

---

## Recommended shares as an HTML file

After every full update the tracker saves **`data/recommended.html`**: one file with today's recommended shares
(short term, long term, avoid) where tapping a share name jumps to its full report. Open it in any browser,
copy it to your phone, or send `/html` to the Telegram bot to receive it there. Make one any time with
`python run.py html`, or use the download button on the Recommended shares page.

## Dashboard pages

| Page | What you see |
|---|---|
| **Ideas & market** | Short-term and long-term ideas with reasons, most important news of the last 48 h with effect / term / "already in price?", FII-DII chart, big-investor deals. Click any row to open the share. |
| **Recommended shares** | Today's recommended shares in three tabs: short term, long term, and avoid / be careful. Tap a share to open its **full report**: the recommendation and every reason behind it, risks, price levels (indicative stop loss and target), all news with its effect and whether it's already in the price, daily bought/sold quantities, bulk/block deals, promoter/FII/DII/mutual fund holdings, big investors and the financial analysis. **Download this report** saves it as a page you can open or share on your phone. |
| **Share details** | Brief and financial health, short/long-term view with reasons, price chart with news markers, daily traded & delivery quantity and spikes, bulk/block deals, who owns it (promoter/FII/DII/MF and change), every news item with its likely effect. |
| **My shares & watchlist** | Add what you bought/sold/want, live P&L, latest reminders. |
| **Reminders** | Full history of reminders. |
| **Data & updates** | Run updates by hand, see which data source worked or failed last time, the **records kept** (how many rows, from which date, source and when last fetched) and **CSV downloads** of news, deals, score history and FII/DII. |

## How the scores work (so you can judge them)

**Short-term score (−100 to +100)** adds up: fresh news weighted by how much of it is *not yet* in the price;
volume spike with price up (+) or down (−); delivery spike with high delivery %; net bulk/block buying or selling;
well-known investor buying/selling; 5-day trend vs 20-day average; overbought RSI (−). Ideas need ≥ 20.

**Learning from past records:** a week after each news item, the tracker records how the price moved over the
next 5 trading days. When similar news (same event type, same direction) has moved prices in the past, today's news
counts more; when it hasn't, it counts less. The report shows this as "In your records, N similar news items moved
the price X% on average". The track record (Recommended page and each report) checks past short-term calls after 5
trading days and long-term calls after 20. These records only build up from the day you start using the tracker.

**Long-term score (0 to 100)** starts from the financial health score and adds: long-term news of the last month,
price vs 200-day average, mutual funds / FIIs increasing holding, promoters reducing (−), well-known investors in
the shareholder list or buying in deals. Ideas need ≥ 60.

All rules live in `stocktracker/nlp.py` (news reading), `stocktracker/analysis.py` (scores) and
`stocktracker/alerts.py` (reminders) and are easy to change.

## Data sources and their limits

All sources are free and need **no API key**.

| Data | Source | Notes |
|---|---|---|
| Share list | NSE `EQUITY_L.csv` + SME list, BSE scrip list API | BSE API sometimes blocks; NSE list still works. |
| News | RSS feeds (ET, Moneycontrol, Mint, BS, NDTV Profit) + Google News RSS | Sites change RSS links occasionally; edit `extra_rss_feeds`. Google News may rate-limit if you search too many companies (`google_news_company_limit`). |
| Daily traded/delivery qty, prices | NSE `sec_bhavdata_full` daily file | Published after ~6 pm; holidays have no file. |
| Longer price history, financials, quarterly results | Yahoo Finance via `yfinance` | Up to 5 years of prices the first time a share is analysed, then daily. Unofficial; occasionally rate-limited or missing small companies. |
| Bulk / block deals | NSE `bulk.csv` / `block.csv` + NSE deals API | **NSE deliberately blocks automated access at times** (HTTP 401/403). The tracker uses browser-like requests and retries; if blocked, that step shows ✗ on the Data page and the rest still works. BSE-only deals are not covered yet. |
| FII / DII | NSE `fiidiiTradeReact` API | Same NSE blocking caveat. |
| Shareholding (promoter / FII / DII / MF, big holders) | NSE shareholding API + XBRL filings | Best-effort parsing of XBRL; a few companies' filings may not split FII/DII/MF correctly. Refreshed for 40 shares per run, rotating. |

"Main persons of the stock market" are found by **name** in bulk/block deal client names and in the shareholder
filings. Big investors often buy through family members or companies with other names, so add those names to
`big_investors` in `config.yaml` when you know them.

**Shares bought vs sold:** every trade has a buyer and a seller, so the exchange publishes one *traded quantity*
per day. The tracker shows that, the *delivery quantity* (shares actually taken home, a better sign of real buying)
and how both changed vs the previous day and the 20-day average.

## Files

```
run.py                 command line
app.py                 dashboard (Streamlit)
config.example.yaml    copy to config.yaml and edit
stocktracker/
  symbols.py           NSE/BSE share list and company-name matching
  news.py              news collection and company matching
  nlp.py               event type, good/bad, short/long term, global themes
  market_data.py       delivery volumes, bulk/block deals, FII/DII, shareholding
  prices.py            price history (Yahoo Finance)
  fundamentals.py      financial health score and brief
  analysis.py          "already in price?", activity spikes, short/long scores, ideas
  portfolio.py         your holdings and watchlist
  alerts.py            reminders with recommendation and reasons
  notify.py            desktop / Telegram / e-mail
  pipeline.py          the daily job
  scheduler.py         automatic running (also starts the Telegram bot)
  bot.py               Telegram bot for your phone
  textviews.py         phone-sized text summaries used by the bot
  report.py            full share report and the recommended-shares HTML file
  demo.py              made-up demo data
data/tracker.db        all collected data (SQLite)
```
