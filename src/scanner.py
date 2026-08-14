"""
Ultimate RSI [LuxAlgo] Scanner — GitHub Actions Edition (v9)
Timeframe : Weekly (1wk candles) for everything
Schedule  : Every Friday 8:00 PM IST (14:30 UTC) + any manual run

ONE track, computed on the WEEKLY timeframe, covering:
  - Indices (Indian / US only — World Indices removed from tracking)
  - Portfolio holdings + Perplexity sector-quality watchlist (holdings.json)

Entry — Accumulation Signal (validated manually on TradingView, EEM weekly chart):
  Ultimate RSI crosses ABOVE its Signal Line, AND on the bar immediately
  before the cross, Ultimate RSI was below 50.
  i.e. "before RSI crosses the signal line, it must have gone below 50."

This is NOT a simple 50-line cross (that would fire on ordinary
momentum recoveries too often). It requires the RSI to have actually
dipped under the midline first, then cross its own signal line back up
— closer in spirit to an oversold-bounce signal than a generic
trend-shift signal.

  Deep accumulation flag: if URSI dipped below 20 within the last ~8
  weekly bars before the crossover, the trigger is additionally flagged
  as "deep accumulation" — a higher-conviction add. Historically these
  produced the largest winners (e.g. MOTHERSON, TRENT). This flag is
  informational only and does not gate the trigger.

This is an "add to position" signal, not first-buy-only — it can be
used for a fresh entry OR to size up an existing holding.

Exit — Trim Warning:
  Ultimate RSI was ≥80 (overbought) on the prior weekly bar and has now
  fallen back below 80. This is a separate, independent check from the
  entry signal above and fires on any tracked instrument (holding or
  watchlist name) whose URSI rolls over from overbought.
  Per the ~30% trim rule: on this reversal, trim roughly 30% of the
  position rather than waiting for a deeper breakdown.

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
URSI_DEEP_ACCUM_LEVEL   = 20.0   # deep accumulation threshold
URSI_DEEP_ACCUM_LOOKBACK = 8     # weekly bars to look back for the deep dip
URSI_TRIM_LEVEL = 80.0           # overbought level — falling back below this triggers a trim warning

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
    # ── Existing portfolio holdings ──────────────────────────────────────
    "HDFCBANK.NS": "HDFC Bank",               "ICICIBANK.NS": "ICICI Bank",
    "RECLTD.NS": "REC Ltd",                   "CIPLA.NS": "Cipla",
    "RELIANCE.NS": "Reliance Industries",     "ONGC.NS": "ONGC",
    "BEL.NS": "Bharat Electronics",           "ASHOKLEY.NS": "Ashok Leyland",
    "NTPC.NS": "NTPC",

    # ── Perplexity sector watchlist — India ──────────────────────────────
    # Financials
    "TCS.NS": "Tata Consultancy Services",    "BAJFINANCE.NS": "Bajaj Finance",
    "KOTAKBANK.NS": "Kotak Mahindra Bank",    "HDFCAMC.NS": "HDFC AMC",
    # Information Technology
    "INFY.NS": "Infosys",                     "HCLTECH.NS": "HCLTech",
    "PERSISTENT.NS": "Persistent Systems",    "LTIM.NS": "LTIMindtree",
    # Healthcare & Pharma
    "SUNPHARMA.NS": "Sun Pharma",             "DIVISLAB.NS": "Divi's Laboratories",
    "DRREDDY.NS": "Dr. Reddy's Laboratories", "APOLLOHOSP.NS": "Apollo Hospitals",
    # Consumer Staples
    "HINDUNILVR.NS": "Hindustan Unilever",    "NESTLEIND.NS": "Nestlé India",
    "BRITANNIA.NS": "Britannia Industries",   "ITC.NS": "ITC",
    "TATACONSUM.NS": "Tata Consumer Products",
    # Consumer Discretionary
    "TITAN.NS": "Titan Company",              "EICHERMOT.NS": "Eicher Motors",
    "BAJAJ-AUTO.NS": "Bajaj Auto",            "MARUTI.NS": "Maruti Suzuki",
    "TRENT.NS": "Trent",
    # Industrials & Capital Goods
    "LT.NS": "Larsen & Toubro",               "SIEMENS.NS": "Siemens India",
    "ABB.NS": "ABB India",                    "HAL.NS": "Hindustan Aeronautics",
    # Materials & Chemicals
    "PIDILITIND.NS": "Pidilite Industries",   "ASIANPAINT.NS": "Asian Paints",
    "ULTRACEMCO.NS": "UltraTech Cement",      "SRF.NS": "SRF",
    "SUPREMEIND.NS": "Supreme Industries",
    # Energy
    "COALINDIA.NS": "Coal India",             "IOC.NS": "Indian Oil",
    "GAIL.NS": "GAIL",
    # Utilities & Power
    "POWERGRID.NS": "Power Grid Corp",        "TATAPOWER.NS": "Tata Power",
    "TORNTPOWER.NS": "Torrent Power",         "CESC.NS": "CESC",
    # Communication Services
    "BHARTIARTL.NS": "Bharti Airtel",         "NAUKRI.NS": "Info Edge",
    "SUNTV.NS": "Sun TV Network",             "ZEEL.NS": "Zee Entertainment",
    "TIPSMUSIC.NS": "Tips Industries",
    # Real Estate
    "DLF.NS": "DLF",                          "LODHA.NS": "Macrotech Developers",
    "PHOENIXLTD.NS": "Phoenix Mills",         "GODREJPROP.NS": "Godrej Properties",
    "PRESTIGE.NS": "Prestige Estates",

    # ── Existing portfolio holdings — US ─────────────────────────────────
    "VOO": "Vanguard S&P 500 ETF",             "EEM": "iShares MSCI Emerging Markets ETF",
    "VTWO": "Vanguard Russell 2000 ETF",       "V": "Visa Inc",
    "IYH": "iShares US Healthcare ETF",        "ABBV": "AbbVie Inc",
    "ACN": "Accenture PLC",                    "GOOGL": "Alphabet Inc (Class A)",
    "NVDA": "NVIDIA Corporation",              "AMZN": "Amazon.com Inc",
    "MSFT": "Microsoft Corporation",           "META": "Meta Platforms",
    "NFLX": "Netflix Inc",                     "TSLA": "Tesla Inc",
    "AAPL": "Apple Inc",                       "BRK-B": "Berkshire Hathaway (Class B)",
    "VEA": "Vanguard FTSE Developed Markets ETF",

    # ── Perplexity sector watchlist — US ─────────────────────────────────
    # Information Technology
    "AVGO": "Broadcom Inc",                    "ADBE": "Adobe Inc",
    # Communication Services
    "TMUS": "T-Mobile US",                     "CMCSA": "Comcast Corp",
    # Financials & Payments
    "MA": "Mastercard Inc",                    "JPM": "JPMorgan Chase",
    "SPGI": "S&P Global Inc",
    # Healthcare & Life Sciences
    "LLY": "Eli Lilly",                        "TMO": "Thermo Fisher Scientific",
    "ABT": "Abbott Laboratories",              "JNJ": "Johnson & Johnson",
    "UNH": "UnitedHealth Group",
    # Consumer Staples
    "COST": "Costco Wholesale",                "PG": "Procter & Gamble",
    "KO": "Coca-Cola",                         "PEP": "PepsiCo",
    "CL": "Colgate-Palmolive",
    # Consumer Discretionary
    "MCD": "McDonald's",                       "HD": "Home Depot",
    "BKNG": "Booking Holdings",                "NKE": "Nike Inc",
    # Industrials
    "WM": "Waste Management",                  "UNP": "Union Pacific",
    "DE": "Deere & Co",                        "HON": "Honeywell",
    "CAT": "Caterpillar Inc",
    # Energy
    "XOM": "Exxon Mobil",                      "CVX": "Chevron Corp",
    "COP": "ConocoPhillips",                   "EOG": "EOG Resources",
    "SLB": "Schlumberger (SLB)",
    # Materials & Chemicals
    "LIN": "Linde plc",                        "ECL": "Ecolab Inc",
    "SHW": "Sherwin-Williams",                 "APD": "Air Products",
    "FCX": "Freeport-McMoRan",
    # Utilities
    "NEE": "NextEra Energy",                   "AWK": "American Water Works",
    "DUK": "Duke Energy",                      "SO": "Southern Company",
    "XEL": "Xcel Energy",
    # Real Estate
    "PLD": "Prologis Inc",                     "EQIX": "Equinix Inc",
    "AMT": "American Tower",                   "O": "Realty Income",
    "WELL": "Welltower Inc",
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
    Evaluates the "Accumulation Signal" entry condition on the latest CLOSED
    weekly bar:
      1. Ultimate RSI crosses ABOVE its Signal Line this bar
         (prev bar: arsi <= signal ; this bar: arsi > signal)
      2. On the bar immediately before the cross, Ultimate RSI was < 50
         ("before crossing, it must have gone below 50")

    Additionally flags "deep accumulation": if URSI dipped below 20 at any
    point in the ~8 weekly bars immediately preceding the crossover bar,
    this is treated as a higher-conviction add — historically these produced
    the largest winners (e.g. MOTHERSON, TRENT). This is informational only;
    it does NOT gate the trigger, it just marks it as higher-conviction.

    This signal is an "add to position" signal, not first-buy-only — it can
    fire on a fresh entry or to size up an existing holding.

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
        # Look back over the ~8 weekly bars BEFORE the crossover bar
        # (i.e. excluding the current, just-crossed bar) for a dip < 20.
        lookback_window = arsi_v.iloc[-(URSI_DEEP_ACCUM_LOOKBACK + 1):-1]
        deep_accum = bool((lookback_window < URSI_DEEP_ACCUM_LEVEL).any())
        deep_accum_low = (
            round(float(lookback_window.min()), 2)
            if len(lookback_window) else None
        )

        return {
            "ursi": round(float(curr_arsi), 2),
            "signal": round(float(curr_signal), 2),
            "prev_ursi": round(float(prev_arsi), 2),
            "deep_accum": deep_accum,
            "deep_accum_low": deep_accum_low,
        }
    return None


def check_ursi_trim(close: pd.Series):
    """
    Evaluates the "Trim Warning" exit condition on the latest CLOSED weekly
    bar: Ultimate RSI was AT OR ABOVE 80 on the prior bar (overbought) and
    has now fallen BELOW 80 this bar — an overbought reversal.

    Per the ~30% trim rule: once URSI starts falling back below 80 after
    running hot, trim roughly 30% of the position rather than waiting for
    a deeper breakdown. This is independent of the accumulation signal
    above and can fire on the same ticker in the same week as an entry
    signal on a different leg, though in practice they're mutually
    exclusive on any single bar (can't be both <50 and >=80 on prior bar).

    Returns a dict with the trigger detail if triggered, else None.
    """
    arsi, signal = calc_ultimate_rsi(close)
    if arsi is None:
        return None

    valid = (~arsi.isna()) & (~signal.isna())
    arsi_v = arsi[valid]
    if len(arsi_v) < 2:
        return None

    curr_arsi, prev_arsi = arsi_v.iloc[-1], arsi_v.iloc[-2]

    if prev_arsi >= URSI_TRIM_LEVEL and curr_arsi < URSI_TRIM_LEVEL:
        return {
            "ursi": round(float(curr_arsi), 2),
            "prev_ursi": round(float(prev_arsi), 2),
        }
    return None


# ══════════════════════════════════════════════════════════════════════════════
# DATA FETCHERS
# ══════════════════════════════════════════════════════════════════════════════

def fetch_yfinance(tickers: list, label: str) -> tuple:
    """
    Fetch WEEKLY OHLCV via Ticker.history(interval='1wk', period='2y').
    Returns (entry_results, trim_results) — two dicts of
    {ticker: trigger_detail_dict}, one for accumulation-signal entries,
    one for overbought trim warnings, for tickers that triggered this week.
    """
    entry_results = {}
    trim_results  = {}
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
                entry_results[ticker] = hit
                deep_tag = f"  💎 DEEP ACCUM (low {hit['deep_accum_low']})" if hit["deep_accum"] else ""
                log.info(f"  🔔 {ticker}: URSI {hit['prev_ursi']} → {hit['ursi']} "
                          f"crossed above signal {hit['signal']}  ← ENTRY TRIGGERED{deep_tag}")

            trim_hit = check_ursi_trim(close)
            if trim_hit:
                trim_results[ticker] = trim_hit
                log.info(f"  ⚠️ {ticker}: URSI {trim_hit['prev_ursi']} → {trim_hit['ursi']} "
                          f"fell below {URSI_TRIM_LEVEL:.0f}  ← TRIM WARNING")
        except Exception as e:
            log.error(f"  ✗ {ticker}: {e}")
    log.info(f"  ✅ {label} done — {len(entry_results)} entry trigger(s), "
              f"{len(trim_results)} trim warning(s) out of {len(tickers)}")
    return entry_results, trim_results


def fetch_crypto(crypto_tickers: list, label: str = "Crypto") -> tuple:
    """
    Crypto now fetched via yfinance using the "-USD" ticker suffix
    (e.g. BTC-USD, ETH-USD, XRP-USD), same weekly interval/period as
    every other instrument in this bot. This replaces the previous
    CoinGecko-based fetch, which was silently failing/rate-limiting on
    GitHub Actions runners and causing crypto to never trigger.
    Returns (entry_results, trim_results).
    """
    return fetch_yfinance(crypto_tickers, label)


# ══════════════════════════════════════════════════════════════════════════════
# WEEKLY LOG (persisted so the last-day-of-month digest can compile every
# weekly notification sent this month — see monthly_digest.py)
# ══════════════════════════════════════════════════════════════════════════════

def append_weekly_log(today: datetime, triggered: dict, trim_triggered: dict, total_scanned: int) -> None:
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
        "trim_triggered": {sym: info for sym, info in trim_triggered.items()},
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


def build_message(triggered: dict, trim_triggered: dict, total_scanned: int, run_type: str) -> str:
    now = datetime.utcnow().strftime("%d %b %Y")

    sections = {
        "🇮🇳 Indian Indices":    {},
        "🇺🇸 US Indices":        {},
        "💼 Portfolio Holdings": {},
    }

    for sym, info in triggered.items():
        if sym in set(INDIAN_INDICES):
            sections["🇮🇳 Indian Indices"][sym] = info
        elif sym in set(US_INDICES):
            sections["🇺🇸 US Indices"][sym] = info
        else:
            sections["💼 Portfolio Holdings"][sym] = info

    trim_sections = {
        "🇮🇳 Indian Indices":    {},
        "🇺🇸 US Indices":        {},
        "💼 Portfolio Holdings": {},
    }
    for sym, info in trim_triggered.items():
        if sym in set(INDIAN_INDICES):
            trim_sections["🇮🇳 Indian Indices"][sym] = info
        elif sym in set(US_INDICES):
            trim_sections["🇺🇸 US Indices"][sym] = info
        else:
            trim_sections["💼 Portfolio Holdings"][sym] = info

    trigger_icon = "🔔 Weekly" if run_type == "scheduled" else "🔍 Manual"
    lines = [
        f"📊 <b>Ultimate RSI Scanner</b>  |  {now} (UTC)",
        f"{trigger_icon}  |  Entry: URSI crosses ↑ signal, having been &lt;50 prior  |  "
        f"Trim: URSI falls back below {URSI_TRIM_LEVEL:.0f}  |  "
        f"Scanned: <b>{total_scanned}</b> instruments",
        "",
    ]

    any_hit = False

    # ── Trim warnings first — exits are time-sensitive ──────────────────
    any_trim = any(items for items in trim_sections.values())
    if any_trim:
        any_hit = True
        lines.append("⚠️ <b>TRIM WARNINGS</b> — overbought reversal")
        for section, items in trim_sections.items():
            if not items:
                continue
            lines.append(f"<b>{section}</b>")
            for sym, info in sorted(items.items(), key=lambda x: -x[1]["prev_ursi"]):
                lines.append(
                    f"  🔻 {get_label(sym)}  →  URSI {info['prev_ursi']} → "
                    f"<b>{info['ursi']}</b>  (fell below {URSI_TRIM_LEVEL:.0f})"
                )
            lines.append("")

    # ── Accumulation / entry signals ─────────────────────────────────────
    for section, items in sections.items():
        if not items:
            continue
        any_hit = True
        lines.append(f"<b>{section}</b>")
        for sym, info in sorted(items.items(), key=lambda x: x[1]["ursi"]):
            deep_tag = ""
            if info.get("deep_accum"):
                deep_tag = f"  💎 <b>DEEP ACCUM</b> (dipped to {info['deep_accum_low']})"
            marker = "💎" if info.get("deep_accum") else "🟢"
            lines.append(
                f"  {marker} {get_label(sym)}  →  URSI {info['prev_ursi']} → "
                f"<b>{info['ursi']}</b> (signal {info['signal']}){deep_tag}"
            )
        lines.append("")

    if not any_hit:
        lines.append("✅ <b>No triggers this week.</b>")
        lines.append("No accumulation crossovers and no trim warnings on any tracked instrument.")
        lines.append("")

    lines += [
        "─────────────────────────",
        "🟢 URSI crossed above its signal line after being below 50 — accumulation signal (add to position, first buy or sizing up).",
        "💎 DEEP ACCUM = URSI dipped below 20 within the last ~8 weekly bars before the cross — historically higher-conviction (e.g. MOTHERSON, TRENT).",
        f"🔻 TRIM WARNING = URSI was ≥{URSI_TRIM_LEVEL:.0f} (overbought) and has now fallen back below {URSI_TRIM_LEVEL:.0f} — "
        f"consider trimming ~30% of the position on the overbought reversal.",
        "💡 <i>Weekly signals only. Confirm before acting. Capital Saturation handled separately.</i>",
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

    total = (len(INDIAN_INDICES) + len(US_INDICES)
             + len(HOLDINGS_NSE_STOCKS) + len(HOLDINGS_US_STOCKS)
             + len(HOLDINGS_CRYPTO))

    log.info("=" * 60)
    log.info("  Ultimate RSI [LuxAlgo] Scanner v9")
    log.info(f"  Run type   : {run_type.upper()}")
    log.info(f"  Entry      : URSI crosses ↑ signal, was <50 prior bar (weekly)")
    log.info(f"  Deep accum : URSI <20 within last {URSI_DEEP_ACCUM_LOOKBACK} weekly bars before cross")
    log.info(f"  Trim       : URSI ≥{URSI_TRIM_LEVEL:.0f} prior bar, falls below {URSI_TRIM_LEVEL:.0f} this bar")
    log.info(f"  Interval   : {YF_INTERVAL}  |  Period: {YF_PERIOD}")
    log.info(f"  Instruments: {total} (indices + holdings)")
    log.info(f"  yfinance   : {yf.__version__}")
    log.info("=" * 60)

    triggered: dict = {}
    trim_triggered: dict = {}

    for tickers, label in [
        (INDIAN_INDICES,        "Indian Indices"),
        (US_INDICES,            "US Indices"),
        (HOLDINGS_NSE_STOCKS,   "Portfolio — NSE Stocks"),
        (HOLDINGS_US_STOCKS,    "Portfolio — US Stocks"),
    ]:
        entry_hits, trim_hits = fetch_yfinance(tickers, label)
        triggered.update(entry_hits)
        trim_triggered.update(trim_hits)

    entry_hits, trim_hits = fetch_crypto(HOLDINGS_CRYPTO, "Portfolio — Crypto")
    triggered.update(entry_hits)
    trim_triggered.update(trim_hits)

    log.info("=" * 60)
    log.info(f"  Total entry triggers : {len(triggered)} / {total}")
    log.info(f"  Total trim warnings  : {len(trim_triggered)} / {total}")
    log.info("=" * 60)

    send_telegram(build_message(triggered, trim_triggered, total_scanned=total, run_type=run_type))

    # Log this week's notification so the last-day-of-month digest
    # (src/monthly_digest.py) can compile everything sent this month.
    if run_type == "scheduled":
        append_weekly_log(today, triggered, trim_triggered, total)


if __name__ == "__main__":
    main()
