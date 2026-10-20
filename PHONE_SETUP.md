# Run the tracker for your phone (free, no computer, no Telegram)

GitHub runs the Python tracker on its servers every weekday at **7:30 am** and **6:30 pm** (India time)
and publishes the result as **your own web page**, locked with your password:

`https://YOUR-GITHUB-NAME.github.io/stock-tracker/`

Open it in Chrome on your phone (tip: Chrome menu → **Add to Home screen** to get an app icon).
It shows your shares and reminders, today's short-term and long-term recommendations with the
reasons and their sources, and a full report for every share. Everything can be set up on an
Android phone in Chrome in about 10 minutes.

You need two files from this chat: `stock-tracker.zip` and `daily.yml`.

## 1. Make a free GitHub account and a repository
1. In Chrome go to **github.com** and sign up (free).
2. Tap **+** (top right) → **New repository**. Name: `stock-tracker`. Choose **Public**
   (free web pages need a public repository; your shares and the page content stay private, see below).
   Tick **Add a README file**. Tap **Create repository**.

## 2. Put the tracker in it
1. Tap **Add file → Upload files**, choose `stock-tracker.zip` from Downloads, tap **Commit changes**.
2. Tap **Add file → Create new file**. In the name box type exactly `.github/workflows/daily.yml`
   (the slashes make folders). Open `daily.yml` from Downloads in Chrome, long-press → **Select all** →
   **Copy**, paste it into the big box, and tap **Commit changes**.

## 3. Add your password and your shares (kept secret)
Repository **Settings** (on a phone it may be under the **⋯** menu) → **Secrets and variables** → **Actions** →
**New repository secret**. Add two secrets:

| Name | Secret |
|---|---|
| `SITE_PASSWORD` | a password you choose for your page |
| `MY_SHARES` | your shares, one per line (see below) |

`MY_SHARES` lines are `share,status,qty,price,target,stop_loss,note`, for example:
```
INFY,bought,10,1500,1800,1380,
Tata Steel,want to buy,,,140,,
```
Status is one of `bought`, `sold`, `short`, `want to buy`, `want to sell`. Share can be the NSE symbol or the
company name. Leave a value empty but keep the commas.

## 4. Turn on the web page
Repository **Settings** → **Pages** → under **Source** choose **GitHub Actions**.

## 5. Run it the first time
**Actions** tab → **Stock tracker** → **Run workflow** → **Run workflow**. The first run takes about 15–25
minutes (it downloads years of prices). When both steps show a green tick, open
`https://YOUR-GITHUB-NAME.github.io/stock-tracker/`, type your password, and tick *Remember on this phone*.

## Every day after that
- **Refresh now:** Actions → Stock tracker → **Run workflow**, then reopen the page after it finishes.
- **Change your shares:** Settings → Secrets and variables → Actions → `MY_SHARES` → ✏️ **Update**.
  GitHub never shows a secret again, so type the whole list each time (keep a copy in your notes).
- **Records** build up on GitHub between runs, so the past-news and track-record parts of the analysis get
  better each week.

## Privacy
- The page is **encrypted with your password** before it is published. Anyone who finds the address only
  sees a password box.
- Your shares are a **secret**: never visible in the public repository.
- The public repository only holds the tracker program itself.

## Good to know
- It's free: public repositories get unlimited GitHub Actions minutes.
- GitHub pauses scheduled runs if the repository has no activity for 60 days. It e-mails you first;
  tapping **Run workflow** keeps it going.
- NSE sometimes blocks big cloud servers. If NSE data (daily quantities, bulk deals, FII/DII) is missing,
  the page says which step failed; news, prices and financials still work.
- Optional phone pop-ups: add a `TELEGRAM_BOT_TOKEN` secret if you ever want reminders on Telegram too.
  Without it, reminders are on the page.
