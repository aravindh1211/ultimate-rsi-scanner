# 📊 Ultimate RSI [LuxAlgo] Weekly Scanner — GitHub Actions

Scans **Indian/World/US Indices and your Portfolio Holdings** (indices +
`holdings.json`) every **Friday at 8:00 PM IST**, on the **Weekly** timeframe.
Sends a **Telegram alert** listing every instrument where LuxAlgo's Ultimate
RSI just crossed **above its signal line, having been below 50 on the prior
bar**. A **consolidated digest** is sent automatically on the last calendar
day of each month, recapping every weekly alert. Runs 100% free on GitHub
Actions.

Capital Saturation is intentionally **not** part of this bot — that's
handled separately in the monthly portfolio-management review.

---

## 🧠 How It Works

Replicates LuxAlgo's Ultimate RSI Pine Script exactly:

| Pine Script | Python |
|---|---|
| `upper/lower = highest/lowest(src, 14)` | Rolling max/min over close, length 14 |
| `diff = ...` (augmented RSI diff logic) | `np.where` on rolling extremes, per LuxAlgo |
| `num/den = rma(diff/abs(diff), 14)` | Wilder's RMA — `rma(diff, 14)` |
| `arsi = num/den*50+50` | Same formula |
| `signal = ema(arsi, 14)` | `pandas.ewm(span=14)` |
| Trigger | `arsi` crosses **above** `signal`, **and** `arsi` was `< 50` on the bar immediately before the cross |

The below-50 requirement is what separates this from a generic midline
cross — the RSI has to have genuinely dipped first, then cross back up
through its own signal line, before it counts as a hit.

---

## 🚀 Full Setup — Step by Step

---

### STEP 1 — Create a Telegram Bot (5 minutes)

1. Open Telegram on your phone or desktop
2. Search for **@BotFather** and open it
3. Send the message: `/newbot`
4. It will ask for a **name** — type anything, e.g. `StochRSI Alerts`
5. It will ask for a **username** — must end in `bot`, e.g. `stochrsi_aravindh_bot`
6. BotFather replies with your token — looks like:
   ```
   7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```
   → **Copy and save this. This is your `TELEGRAM_BOT_TOKEN`.**

7. Now get your **Chat ID**:
   - Click **Start** in your new bot's chat window (this activates it)
   - Open this URL in your browser (replace `YOUR_TOKEN`):
     ```
     https://api.telegram.org/botYOUR_TOKEN/getUpdates
     ```
   - Look for `"chat":{"id":XXXXXXXXX}` in the response
   - That number is your **`TELEGRAM_CHAT_ID`** (can be negative for groups)

---

### STEP 2 — Create a GitHub Repository

1. Go to [github.com](https://github.com) — sign up free if needed
2. Click **New repository** (top right `+` → New repository)
3. Settings:
   - **Repository name:** `stochrsi-scanner`
   - **Visibility:** Private ✅
   - Leave everything else default
4. Click **Create repository**

---

### STEP 3 — Upload the Files

Upload these files **exactly** in this folder structure:

```
stochrsi-scanner/               ← root of your repo
├── .github/
│   └── workflows/
│       └── scanner.yml         ← GitHub Actions trigger
├── src/
│   └── scanner.py              ← main scanner logic
└── requirements.txt            ← Python dependencies
```

**To upload:**
1. On your new repo page, click **uploading an existing file**
2. First create the folders by uploading `scanner.yml` — GitHub will ask you for the path,
   type `.github/workflows/scanner.yml` in the path field
3. Then upload `src/scanner.py` with path `src/scanner.py`
4. Then upload `requirements.txt` at the root

> **Easier alternative:** Use [GitHub Desktop](https://desktop.github.com/) — clone the repo,
> copy files into the folder, commit and push.

---

### STEP 4 — Add Telegram Secrets

Your bot token and chat ID must never be hardcoded. GitHub Secrets keeps them safe.

1. In your repo, go to **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret** and add these two:

   | Secret Name | Value |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | The token from BotFather (Step 1) |
   | `TELEGRAM_CHAT_ID` | Your chat ID number (Step 1) |

3. Optionally add a **variable** (not secret) for threshold:
   - Go to **Variables** tab → **New repository variable**
   - Name: `STOCH_RSI_THRESHOLD` → Value: `10`
   - (If you skip this, it defaults to 10 automatically)

---

### STEP 5 — Test It Right Now (Manual Trigger)

Don't wait until tomorrow morning — trigger it immediately:

1. In your repo, click the **Actions** tab
2. Click **StochRSI Daily Scanner** in the left sidebar
3. Click **Run workflow** → **Run workflow** (green button)
4. Watch it run — click the job to see live logs
5. Check your Telegram — message arrives within ~2 minutes ✅

---

### STEP 6 — Automatic Daily Schedule

The workflow already has the schedule set:
```yaml
- cron: "30 3 * * *"   # = 9:00 AM IST every day
```

No action needed — once the file is in your repo, GitHub runs it automatically every morning.

> ⚠️ **Note:** GitHub may pause scheduled workflows on repos with no activity for 60 days.
> To prevent this: occasionally open the repo or push a small change.
> You can also re-enable it from the Actions tab if it gets paused.

---

## 📬 Sample Telegram Message

```
📊 StochRSI Daily Scanner  |  16 Jun 2026 (UTC)
🔔 Stoch RSI K ≤ 10  |  Scanned: 63 instruments

🇮🇳 NSE Stocks
  🟢 WIPRO  →  K = 3.81
  🟡 SUNPHARMA  →  K = 8.42

🇺🇸 US Stocks
  🟢 INTC  →  K = 5.14

🪙 Crypto
  🟢 ETHEREUM  →  K = 2.77
  🟡 SOLANA  →  K = 9.03

─────────────────────────
🟢 K ≤ 5  →  Deeply oversold
🟡 K 5–10  →  Oversold zone
💡 Use as an accumulation signal, not a standalone entry.
```

---

## ✏️ Customising Your Watchlist

Open `src/scanner.py` and edit the lists at the top:

```python
NSE_STOCKS = [
    "RELIANCE.NS", "TCS.NS", ...   # Any NSE ticker — must end with .NS
]

US_STOCKS = [
    "AAPL", "NVDA", ...            # US ticker as-is
]

GLOBAL_INDICES = [
    "^NSEI", "^GSPC", ...          # Yahoo Finance index codes
]

CRYPTO_IDS = [
    "bitcoin", "solana", ...       # CoinGecko IDs (find at coingecko.com)
]
```

**Useful index codes:**
| Index | Code |
|---|---|
| Nifty 50 | `^NSEI` |
| Bank Nifty | `^NSEBANK` |
| BSE Sensex | `^BSESN` |
| S&P 500 | `^GSPC` |
| NASDAQ | `^IXIC` |
| Gold Futures | `GC=F` |
| Crude Oil | `CL=F` |

---

## ⏰ Changing the Schedule

Edit `.github/workflows/scanner.yml`:
```yaml
schedule:
  - cron: "30 3 * * *"    # Current: 9:00 AM IST daily
```

Cron format: `minute  hour  day  month  weekday`

| Schedule | Cron |
|---|---|
| 7:30 AM IST daily | `0 2 * * *` |
| 9:00 AM IST, weekdays only | `30 3 * * 1-5` |
| 9:00 AM IST + 3:30 PM IST | `30 3,10 * * *` |

---

## 🆓 GitHub Actions Free Tier

| Limit | Free Allowance | Your Usage |
|---|---|---|
| Minutes/month | 2,000 min | ~5 min/day = ~150 min/month |
| Storage | 500 MB | Negligible |
| Concurrent jobs | 20 | 1 |

**You use less than 10% of the free quota.** No billing ever needed for this use case.

---

## 🔧 Changing the Alert Threshold

**Option A — Permanent change:**
Go to repo **Settings → Secrets and variables → Actions → Variables**
Update `STOCH_RSI_THRESHOLD` to any value (e.g. `15` or `20`)

**Option B — One-time manual run:**
Actions tab → Run workflow → enter a threshold in the input box before clicking Run

---

## ❓ Troubleshooting

| Problem | Fix |
|---|---|
| No Telegram message received | Check token/chat ID in Secrets; confirm you clicked Start in the bot |
| Workflow not running automatically | Check Actions tab → re-enable if paused |
| Ticker showing "insufficient data" | Yahoo Finance may not support it — try removing it |
| `getUpdates` returns empty JSON | Send any message to your bot first, then retry |
