"""
Ultimate RSI [LuxAlgo] Scanner — GitHub Actions Edition (v6)
Timeframe : Weekly (1wk candles) for everything
Schedule  : Every Friday 8:00 PM IST (14:30 UTC) + any manual run

ONE track, computed on the WEEKLY timeframe, covering exactly the
universe already tracked by the previous StochRSI bot's weekly track:
  - Indices (Indian / World / US)
  - Portfolio holdings (from holdings.json)

Trigger condition (validated manually on TradingView, EEM weekly chart):
  Ultimate RSI crosses ABOVE its Signal Line, AND on the bar immediately
  before the cross, Ultimate RSI was below 50.
  i.e. "before RSI crosses the signal line, it must have gone below 50."

This is NOT a simple 50-line cross (that would fire on ordinary
momentum recoveries too often). It requires the RSI to have actually
dipped under the midline first, then cross its own signal line back up
— closer in spirit to an oversold-bounce signal than a generic
trend-shift signal.

Capital Saturation is intentionally NOT part of this bot — handled
separately in the monthly portfolio review.

Replicates the LuxAlgo Pine Script exactly:
    upper = highest(src, length);  lower = lowest(src, length)
    r     = upper - lower
    diff  = upper > upper[1] ? r : lower < lower[1] ? -r : (src - src[1])
    num   = rma(diff, length);     den = rma(abs(diff), length)
    arsi  = num / den * 50 + 50
    signal = ema(arsi, smooth)

Data sources:
  yfinance   — equities + indices + crypto (interval='1wk', period='2y')
              Crypto tickers use the Yahoo Finance "-USD" suffix format,
              e.g. BTC-USD, ETH-USD, XRP-USD (see holdings.json).
"""

import os
import json
import logging
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
WEEKLY_LOG_FILE    = os.environ.get("WEEKLY_LOG_FILE", "state/weekly_log.json")

# Ultimate RSI settings (LuxAlgo defaults)
URSI_LENGTH  = int(os.environ.get("URSI_LENGTH", "14"))
URSI_SMOOTH  = int(os.environ.get("URSI_SMOOTH", "14"))   # signal line EMA length
URSI_MIDLINE = 50.0

# Weekly fetch settings
YF_INTERVAL = "1wk"
YF_PERIOD   = "2y"    # 2 years of weekly bars = ~104 candles, comfortably above minimum


# ══════════════════════════════════════════════════════════════════════════════
# WATCHLISTS  (identical universe to what the previous bot's WEEKLY track used)
# ══════════════════════════════════════════════════════════════════════════════

# ── Indian Indices ──────────────────────────────────────────────────────────────
INDIAN_INDICES = [
    "^NSEI",          # Nifty 50
    "^NSEBANK",       # Bank Nifty
    "^CNXSC",         # Nifty SmallCap 250
    "^NSEMDCP50",     # Nifty MidCap 150
    "^CNXINFRA",      # Nifty Infrastructure
    "^CNXIT",         # Nifty IT
    "^CNXPHARMA",     # Nifty Pharma
    "^CNXAUTO",       # Nifty Auto
    "^CNXPSUBANK",    # Nifty PSU Bank
    "^CNXFMCG",       # Nifty FMCG
    "^CNXENERGY",     # Nifty Energy
    "^CNXHEALTH",     # Nifty Healthcare
    "^CNXMETAL",      # Nifty Metal
    "^CNXMEDIA",      # Nifty Media
    "^CNXREALTY",     # Nifty Realty
    "^CNXPSE",        # Nifty PSE
    "^CNXCMDT",       # Nifty Commodities
    "^CNXSERVICE",    # Nifty Services Sector
    "^CNXMNC",        # Nifty MNC
    "^CNXCONSUM",     # Nifty Consumption
    "^CNXFIN",        # Nifty Financial Services
    "^BSESN",         # BSE Sensex
    "GC=F",           # Gold (USD/oz)
    "SI=F",           # Silver (USD/oz)
    "GOLDBEES.NS",    # Gold/INR proxy (Nippon Gold BeES ETF)
    "INFRABEES.NS",   # Nifty 500 Multicap Infra proxy (Nippon Infra BeES ETF)
]

# ── Major World Indices ──────────────────────────────────────────────────────────
WORLD_INDICES = [
    "^GSPC",          # S&P 500
    "^NDX",           # Nasdaq 100
    "^DJI",           # Dow Jones Industrial
    "^FTSE",          # FTSE 100 (UK)
    "^GDAXI",         # DAX 40 (Germany)
    "^FCHI",          # CAC 40 (France)
    "^N225",          # Nikkei 225 (Japan)
    "^HSI",           # Hang Seng (HK)
    "000001.SS",      # Shanghai Composite
    "^KS11",          # KOSPI (South Korea)
    "^AXJO",          # ASX 200 (Australia)
    "^STI",           # Straits Times (Singapore)
    "^TWII",          # Taiwan Weighted
    "^MXX",           # IPC Mexico
    "^BVSP",          # Bovespa (Brazil)
    "^AEX",           # AEX (Netherlands)
    "^SSMI",          # SMI (Switzerland)
    "FTSEMIB.MI",     # FTSE MIB (Italy)
    "^IBEX",          # IBEX 35 (Spain)
]

# ── US Indices ────────────────────────────────────────────────────────────────
US_INDICES = [
    "^RUT",           # Russell 2000
    "^RUI",           # Russell 1000
    "^RUA",           # Russell 3000
    "^MID",           # S&P MidCap 400
    "^SML",           # S&P SmallCap 600
    "^IXIC",          # Nasdaq Composite
    "^NYA",           # NYSE Composite
    "^XAX",           # NYSE American Composite
    "^DJT",           # Dow Jones Transport
    "^DJU",           # Dow Jones Utilities
    "^VIX",           # CBOE Volatility Index
    "^W5000",         # Wilshire 5000
    "^OEX",           # S&P 100
    "^XND",           # Nasdaq 100 Equal Weight
    "^SOX",           # Philadelphia Semiconductor
]

# ── Portfolio holdings ──────────────────────────────────────────────────────────
# Loaded from holdings.json (repo root) so the list can be updated without
# touching this script — either by editing holdings.json directly, or via
# the "Update Holdings" workflow (.github/workflows/update-holdings.yml).
HOLDINGS_FILE = os.environ.get("HOLDINGS_FILE", "holdings.json")


def _load_holdings(path: str) -> dict:
    default = {"nse_stocks": [], "us_stocks": [], "crypto": []}
    if not os.path.exists(path):
        log.warning(f"  ⚠ Holdings file '{path}' not found — scanning 0 holdings")
        return default
    try:
        with open(path, "r") as f:
            data = json.load(f)
        for key in default:
            data.setdefault(key, [])
        return data
    except Exception as e:
        log.error(f"  ✗ Failed to load holdings file: {e}")
        return default


_holdings = _load_holdings(HOLDINGS_FILE)
HOLDINGS_NSE_STOCKS = _holdings["nse_stocks"]
HOLDINGS_US_STOCKS  = _holdings["us_stocks"]
HOLDINGS_CRYPTO     = _holdings["crypto"]

# ── Display Labels ─────────────────────────────────────────────────────────────
INDEX_LABELS = {
    "^NSEI":       "Nifty 50",          "^NSEBANK":    "Bank Nifty",
    "^CNXSC":      "Nifty SmallCap 250","^NSEMDCP50":  "Nifty MidCap 150",
    "^CNXINFRA":   "Nifty Infra",       "^CNXIT":      "Nifty IT",
    "^CNXPHARMA":  "Nifty Pharma",      "^CNXAUTO":    "Nifty Auto",
    "^CNXPSUBANK": "Nifty PSU Bank",    "^CNXFMCG":    "Nifty FMCG",
    "^CNXENERGY":  "Nifty Energy",      "^CNXHEALTH":  "Nifty Healthcare",
    "^CNXMETAL":   "Nifty Metal",       "^CNXMEDIA":   "Nifty Media",
    "^CNXREALTY":  "Nifty Realty",      "^CNXPSE":     "Nifty PSE",
    "^CNXCMDT":    "Nifty Commodities", "^CNXSERVICE": "Nifty Services Sector",
    "^CNXMNC":     "Nifty MNC",         "^CNXCONSUM":  "Nifty Consumption",
    "^CNXFIN":     "Nifty Financial Services",
    "^BSESN":      "BSE Sensex",        "GC=F":        "Gold (USD/oz)",
    "SI=F":        "Silver (USD/oz)",   "GOLDBEES.NS": "Gold/INR (GoldBees)",
    "INFRABEES.NS":"Infra (InfraBees)",
    "^GSPC":       "S&P 500",           "^NDX":        "Nasdaq 100",
    "^DJI":        "Dow Jones",         "^FTSE":       "FTSE 100",
    "^GDAXI":      "DAX 40",            "^FCHI":       "CAC 40",
    "^N225":       "Nikkei 225",        "^HSI":        "Hang Seng",
    "000001.SS":   "Shanghai Composite","^KS11":       "KOSPI",
    "^AXJO":       "ASX 200",           "^STI":        "Straits Times",
    "^TWII":       "Taiwan Weighted",   "^MXX":        "IPC Mexico",
    "^BVSP":       "Bovespa",           "^AEX":        "AEX",
    "^SSMI":       "SMI",               "FTSEMIB.MI":  "FTSE MIB",
    "^IBEX":       "IBEX 35",
    "^RUT":        "Russell 2000",      "^RUI":        "Russell 1000",
    "^RUA":        "Russell 3000",      "^MID":        "S&P MidCap 400",
    "^SML":        "S&P SmallCap 600",  "^IXIC":       "Nasdaq Composite",
    "^NYA":        "NYSE Composite",    "^XAX":        "NYSE American",
    "^DJT":        "DJ Transport",      "^DJU":        "DJ Utilities",
    "^VIX":        "VIX",               "^W5000":      "Wilshire 5000",
    "^OEX":        "S&P 100",           "^XND":        "Nasdaq 100 EW",
    "^SOX":        "Philadelphia Semi",
}

STOCK_LABELS = {
    "HDFCBANK.NS": "HDFC Bank",               "ICICIBANK.NS": "ICICI Bank",
    "RECLTD.NS": "REC Ltd",                   "CIPLA.NS": "Cipla",
    "RELIANCE.NS": "Reliance Industries",     "TCS.NS": "Tata Consultancy Services",
    "ONGC.NS": "ONGC",                        "BEL.NS": "Bharat Electronics",
    "ASHOKLEY.NS": "Ashok Leyland",           "NTPC.NS": "NTPC",
    "IRFC.NS": "Indian Railway Finance Corp",

    "VOO": "Vanguard S&P 500 ETF",             "EEM": "iShares MSCI Emerging Markets ETF",
    "VTWO": "Vanguard Russell 2000 ETF",       "MRK": "Merck & Co",
    "IYH": "iShares US Healthcare ETF",        "V": "Visa Inc",
    "ABBV": "AbbVie Inc",                      "BRK-B": "Berkshire Hathaway (Class B)",
    "ACN": "Accenture PLC",                    "JNJ": "Johnson & Johnson",
    "GOOGL": "Alphabet Inc (Class A)",         "NVDA": "NVIDIA Corporation",
    "AMZN": "Amazon.com Inc",                  "MSFT": "Microsoft Corporation",
    "META": "Meta Platforms",                  "NFLX": "Netflix Inc",
    "TSLA": "Tesla Inc",                       "AAPL": "Apple Inc",
    "VEA": "Vanguard FTSE Developed Markets ETF",
}

CRYPTO_LABELS = {
    "BTC-USD": "Bitcoin (BTC)",       "ETH-USD": "Ethereum (ETH)",
    "XRP-USD": "XRP (Ripple)",        "BNB-USD": "BNB (BNB)",
    "SOL-USD": "Solana (SOL)",        "ADA-USD": "Cardano (ADA)",
    "DOGE-USD": "Dogecoin (DOGE)",    "AVAX-USD": "Avalanche (AVAX)",
    "LINK-USD": "Chainlink (LINK)",   "DOT-USD": "Polkadot (DOT)",
}


# ══════════════════════════════════════════════════════════════════════════════
# ULTIMATE RSI LOGIC  (LuxAlgo Pine Script exact replication)
# ══════════════════════════════════════════════════════════════════════════════

def rma(series: pd.Series, length: int) -> pd.Series:
    """Wilder's RMA — seeds from SMA of first `length` bars. Matches Pine rma()."""
    alpha  = 1.0 / length
    result = np.full(len(series), np.nan)
    if len(series) < length:
        return pd.Series(result, index=series.index)
    result[length - 1] = series.iloc[:length].mean()
    for i in range(length, len(series)):
        result[i] = alpha * series.iloc[i] + (1 - alpha) * result[i - 1]
    return pd.Series(result, index=series.index)


def ema(series: pd.Series, length: int) -> pd.Series:
    """Matches Pine ta.ema()."""
    return series.ewm(span=length, adjust=False).mean()


def calc_ultimate_rsi(close: pd.Series, length: int = URSI_LENGTH,
                       smooth: int = URSI_SMOOTH):
    """
    Returns (ursi_series, signal_series) — full history, so the caller can
    inspect the last two closed bars for a crossover.

    Pine Script:
        upper = highest(src, length); lower = lowest(src, length)
        r     = upper - lower
        d     = src - src[1]
        diff  = upper > upper[1] ? r : lower < lower[1] ? -r : d
        num   = rma(diff, length);  den = rma(abs(diff), length)
        arsi  = num / den * 50 + 50
        signal = ema(arsi, smooth)      [LuxAlgo default smoType2 = 'EMA']
    """
    min_bars = length + smooth + 5
    if len(close) < min_bars:
        return None, None

    upper = close.rolling(length).max()
    lower = close.rolling(length).min()
    r     = upper - lower
    d     = close.diff()

    upper_rising  = upper > upper.shift(1)
    lower_falling = lower < lower.shift(1)

    diff = pd.Series(
        np.where(upper_rising, r,
        np.where(lower_falling, -r, d)),
        index=close.index,
    )

    num = rma(diff, length)
    den = rma(diff.abs(), length)
    arsi = (num / den) * 50 + 50
    signal = ema(arsi.dropna(), smooth)

    # Re-align signal onto the full index (leading NaNs where arsi was NaN)
    signal = signal.reindex(close.index)

    return arsi, signal


def check_ursi_cross(close: pd.Series):
    """
    Evaluates the trigger condition on the latest CLOSED weekly bar:
      1. Ultimate RSI crosses ABOVE its Signal Line this bar
         (prev bar: arsi <= signal ; this bar: arsi > signal)
      2. On the bar immediately before the cross, Ultimate RSI was < 50
         ("before crossing, it must have gone below 50")

    Returns a dict with the trigger detail if triggered, else None.
    """
    arsi, signal = calc_ultimate_rsi(close)
    if arsi is None:
        return None

    valid = (~arsi.isna()) & (~signal.isna())
    arsi_v, signal_v = arsi[valid], signal[valid]
    if len(arsi_v) < 3:
        return None

    curr_arsi,   prev_arsi   = arsi_v.iloc[-1],   arsi_v.iloc[-2]
    curr_signal, prev_signal = signal_v.iloc[-1], signal_v.iloc[-2]

    crossed_up   = (prev_arsi <= prev_signal) and (curr_arsi > curr_signal)
    was_below_50 = prev_arsi < URSI_MIDLINE

    if crossed_up and was_below_50:
        return {
            "ursi": round(float(curr_arsi), 2),
            "signal": round(float(curr_signal), 2),
            "prev_ursi": round(float(prev_arsi), 2),
        }
    return None


# ══════════════════════════════════════════════════════════════════════════════
# DATA FETCHERS
# ══════════════════════════════════════════════════════════════════════════════

def fetch_yfinance(tickers: list, label: str) -> dict:
    """
    Fetch WEEKLY OHLCV via Ticker.history(interval='1wk', period='2y').
    Returns {ticker: trigger_detail_dict} — only tickers that triggered this week.
    """
    results = {}
    log.info(f"── {label}: {len(tickers)} tickers  [Weekly / 2y]")
    for ticker in tickers:
        try:
            df = yf.Ticker(ticker).history(
                period=YF_PERIOD,
                interval=YF_INTERVAL,
                auto_adjust=True,
            )
            if df is None or df.empty:
                log.warning(f"  ⚠ {ticker}: no data")
                continue
            if len(df) < URSI_LENGTH + URSI_SMOOTH + 5:
                log.warning(f"  ⚠ {ticker}: only {len(df)} weekly bars (need more)")
                continue
            close = df["Close"].squeeze().dropna()
            hit = check_ursi_cross(close)
            if hit:
                results[ticker] = hit
                log.info(f"  🔔 {ticker}: URSI {hit['prev_ursi']} → {hit['ursi']} "
                          f"crossed above signal {hit['signal']}  ← TRIGGERED")
        except Exception as e:
            log.error(f"  ✗ {ticker}: {e}")
    log.info(f"  ✅ {label} done — {len(results)} trigger(s) out of {len(tickers)}")
    return results


def fetch_crypto(crypto_tickers: list, label: str = "Crypto") -> dict:
    """
    Crypto now fetched via yfinance using the "-USD" ticker suffix
    (e.g. BTC-USD, ETH-USD, XRP-USD), same weekly interval/period as
    every other instrument in this bot. This replaces the previous
    CoinGecko-based fetch, which was silently failing/rate-limiting on
    GitHub Actions runners and causing crypto to never trigger.
    Returns {ticker: trigger_detail_dict}.
    """
    return fetch_yfinance(crypto_tickers, label)


# ══════════════════════════════════════════════════════════════════════════════
# WEEKLY LOG (persisted so the last-day-of-month digest can compile every
# weekly notification sent this month — see monthly_digest.py)
# ══════════════════════════════════════════════════════════════════════════════

def append_weekly_log(today: datetime, triggered: dict, total_scanned: int) -> None:
    os.makedirs(os.path.dirname(WEEKLY_LOG_FILE) or ".", exist_ok=True)
    entries = []
    if os.path.exists(WEEKLY_LOG_FILE):
        try:
            with open(WEEKLY_LOG_FILE, "r") as f:
                entries = json.load(f)
        except Exception as e:
            log.error(f"  ✗ Failed to load weekly log, starting fresh: {e}")
            entries = []

    entries.append({
        "date": today.strftime("%Y-%m-%d"),
        "total_scanned": total_scanned,
        "triggered": {sym: info for sym, info in triggered.items()},
    })

    with open(WEEKLY_LOG_FILE, "w") as f:
        json.dump(entries, f, indent=2, sort_keys=True)
    log.info(f"  📝 Weekly log updated — {len(entries)} week(s) logged so far this month")


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════════════════════════

def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("❌ Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return False
    url  = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, data=data, timeout=15)
        if resp.status_code == 200:
            log.info("✅ Telegram sent")
            return True
        log.error(f"Telegram {resp.status_code}: {resp.text}")
        return False
    except Exception as e:
        log.error(f"Telegram failed: {e}")
        return False


def get_label(sym: str) -> str:
    if sym in INDEX_LABELS:
        return f"{INDEX_LABELS[sym]} ({sym})"
    if sym in STOCK_LABELS:
        return f"{STOCK_LABELS[sym]} ({sym})"
    if sym in CRYPTO_LABELS:
        return CRYPTO_LABELS[sym]
    return sym.replace(".NS", "").replace("^", "")


def build_message(triggered: dict, total_scanned: int, run_type: str) -> str:
    now = datetime.utcnow().strftime("%d %b %Y")

    sections = {
        "🇮🇳 Indian Indices":    {},
        "🌍 World Indices":      {},
        "🇺🇸 US Indices":        {},
        "💼 Portfolio Holdings": {},
    }

    for sym, info in triggered.items():
        if sym in set(INDIAN_INDICES):
            sections["🇮🇳 Indian Indices"][sym] = info
        elif sym in set(WORLD_INDICES):
            sections["🌍 World Indices"][sym] = info
        elif sym in set(US_INDICES):
            sections["🇺🇸 US Indices"][sym] = info
        else:
            sections["💼 Portfolio Holdings"][sym] = info

    trigger_icon = "🔔 Weekly" if run_type == "scheduled" else "🔍 Manual"
    lines = [
        f"📊 <b>Ultimate RSI Scanner</b>  |  {now} (UTC)",
        f"{trigger_icon}  |  Signal: URSI crosses ↑ signal line, having been &lt;50 prior  "
        f"|  Scanned: <b>{total_scanned}</b> instruments",
        "",
    ]

    any_hit = False
    for section, items in sections.items():
        if not items:
            continue
        any_hit = True
        lines.append(f"<b>{section}</b>")
        for sym, info in sorted(items.items(), key=lambda x: x[1]["ursi"]):
            lines.append(
                f"  🟢 {get_label(sym)}  →  URSI {info['prev_ursi']} → "
                f"<b>{info['ursi']}</b> (signal {info['signal']})"
            )
        lines.append("")

    if not any_hit:
        lines.append("✅ <b>No triggers this week.</b>")
        lines.append("No Ultimate RSI cross-above-signal (from below 50) on any tracked instrument.")
        lines.append("")

    lines += [
        "─────────────────────────",
        "🟢 URSI crossed above its signal line after being below 50 — potential oversold-bounce.",
        "💡 <i>Weekly signals only. Confirm before entry. Capital Saturation handled separately.</i>",
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    today = datetime.utcnow()

    # Detect if this is a scheduled Friday run or a manual trigger
    run_type = "manual"
    if today.weekday() == 4:   # 4 = Friday
        run_type = "scheduled"

    total = (len(INDIAN_INDICES) + len(WORLD_INDICES) + len(US_INDICES)
             + len(HOLDINGS_NSE_STOCKS) + len(HOLDINGS_US_STOCKS)
             + len(HOLDINGS_CRYPTO))

    log.info("=" * 60)
    log.info("  Ultimate RSI [LuxAlgo] Scanner v6")
    log.info(f"  Run type   : {run_type.upper()}")
    log.info(f"  Condition  : URSI crosses ↑ signal, was <50 prior bar")
    log.info(f"  Interval   : {YF_INTERVAL}  |  Period: {YF_PERIOD}")
    log.info(f"  Instruments: {total} (indices + holdings)")
    log.info(f"  yfinance   : {yf.__version__}")
    log.info("=" * 60)

    triggered: dict = {}
    triggered.update(fetch_yfinance(INDIAN_INDICES, "Indian Indices"))
    triggered.update(fetch_yfinance(WORLD_INDICES,  "World Indices"))
    triggered.update(fetch_yfinance(US_INDICES,     "US Indices"))
    triggered.update(fetch_yfinance(HOLDINGS_NSE_STOCKS, "Portfolio — NSE Stocks"))
    triggered.update(fetch_yfinance(HOLDINGS_US_STOCKS,  "Portfolio — US Stocks"))
    triggered.update(fetch_crypto(HOLDINGS_CRYPTO, "Portfolio — Crypto"))

    log.info("=" * 60)
    log.info(f"  Total triggered : {len(triggered)} / {total}")
    log.info("=" * 60)

    send_telegram(build_message(triggered, total_scanned=total, run_type=run_type))

    # Log this week's notification so the last-day-of-month digest
    # (src/monthly_digest.py) can compile everything sent this month.
    if run_type == "scheduled":
        append_weekly_log(today, triggered, total)


if __name__ == "__main__":
    main()
