"""
One-off retrospective check — Ultimate RSI Scanner
Run manually: `python scripts/backfill_4weeks.py`

Replays the trigger condition (URSI crosses above signal, having been
< 50 on the prior bar) across each of the last N closed weekly bars,
not just the latest one — so you can see what WOULD have fired each
of the past 4 Fridays, even though the bot only started running now.

Sends ONE consolidated Telegram message covering all N weeks, grouped
by week (most recent last). Does NOT touch state/weekly_log.json —
this is a read-only, one-time lookback, independent of the regular
weekly/monthly cadence.
"""

import os
import sys
from datetime import datetime, timedelta

# Make src/ importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import yfinance as yf  # noqa: E402
from scanner import (  # noqa: E402
    INDIAN_INDICES, WORLD_INDICES, US_INDICES,
    HOLDINGS_NSE_STOCKS, HOLDINGS_US_STOCKS, HOLDINGS_CRYPTO,
    URSI_LENGTH, URSI_SMOOTH, URSI_MIDLINE,
    calc_ultimate_rsi, get_label, send_telegram,
    YF_PERIOD, YF_INTERVAL,
)
import pandas as pd  # noqa: E402

LOOKBACK_WEEKS = int(os.environ.get("LOOKBACK_WEEKS", "4"))


def find_triggers_over_lookback(close: pd.Series, weeks: int) -> list:
    """
    Returns a list of dicts, one per closed bar in the last `weeks` bars,
    for any bar where the cross condition was true AT THAT BAR (using
    only data available up to and including that bar — no lookahead).
    """
    arsi, signal = calc_ultimate_rsi(close)
    if arsi is None:
        return []

    valid = (~arsi.isna()) & (~signal.isna())
    arsi_v, signal_v = arsi[valid], signal[valid]
    if len(arsi_v) < weeks + 1:
        return []

    hits = []
    # Check each of the last `weeks` bars as if it were "today"
    for i in range(-weeks, 0):
        curr_arsi, prev_arsi = arsi_v.iloc[i], arsi_v.iloc[i - 1]
        curr_signal, prev_signal = signal_v.iloc[i], signal_v.iloc[i - 1]
        crossed_up = (prev_arsi <= prev_signal) and (curr_arsi > curr_signal)
        was_below_50 = prev_arsi < URSI_MIDLINE
        if crossed_up and was_below_50:
            hits.append({
                "date": arsi_v.index[i].strftime("%d %b %Y"),
                "ursi": round(float(curr_arsi), 2),
                "signal": round(float(curr_signal), 2),
                "prev_ursi": round(float(prev_arsi), 2),
            })
    return hits


def scan_yfinance_group(tickers: list, label: str, weeks: int) -> dict:
    results = {}
    print(f"── {label}: {len(tickers)} tickers")
    for ticker in tickers:
        try:
            df = yf.Ticker(ticker).history(period=YF_PERIOD, interval=YF_INTERVAL, auto_adjust=True)
            if df is None or df.empty or len(df) < URSI_LENGTH + URSI_SMOOTH + weeks + 5:
                print(f"  ⚠ {ticker}: insufficient data")
                continue
            close = df["Close"].squeeze().dropna()
            hits = find_triggers_over_lookback(close, weeks)
            if hits:
                results[ticker] = hits
                print(f"  🔔 {ticker}: {len(hits)} trigger(s) in lookback")
        except Exception as e:
            print(f"  ✗ {ticker}: {e}")
    return results


def build_backfill_message(all_hits: dict, weeks: int) -> str:
    now = datetime.utcnow().strftime("%d %b %Y")
    lines = [
        f"🕰️ <b>Ultimate RSI — {weeks}-Week Retrospective Check</b>  |  run on {now} (UTC)",
        "One-time lookback — checks what WOULD have triggered on each of the "
        f"last {weeks} closed weekly bars.",
        "",
    ]

    if not all_hits:
        lines.append(f"✅ <b>No triggers found in the last {weeks} weeks</b> across any tracked instrument.")
        return "\n".join(lines)

    # Flatten and group by date for a chronological view
    by_date: dict = {}
    for sym, hits in all_hits.items():
        for h in hits:
            by_date.setdefault(h["date"], []).append((sym, h))

    for date in sorted(by_date.keys(), key=lambda d: datetime.strptime(d, "%d %b %Y")):
        lines.append(f"<b>Week of {date}</b>")
        for sym, h in sorted(by_date[date], key=lambda x: x[1]["ursi"]):
            lines.append(
                f"  🟢 {get_label(sym)}  →  URSI {h['prev_ursi']} → "
                f"<b>{h['ursi']}</b> (signal {h['signal']})"
            )
        lines.append("")

    lines += [
        "─────────────────────────",
        "💡 <i>Retrospective only — not a live signal. Capital Saturation not checked here.</i>",
    ]
    return "\n".join(lines)


def main():
    print(f"Running {LOOKBACK_WEEKS}-week retrospective backfill...")

    all_hits: dict = {}
    all_hits.update(scan_yfinance_group(INDIAN_INDICES, "Indian Indices", LOOKBACK_WEEKS))
    all_hits.update(scan_yfinance_group(WORLD_INDICES, "World Indices", LOOKBACK_WEEKS))
    all_hits.update(scan_yfinance_group(US_INDICES, "US Indices", LOOKBACK_WEEKS))
    all_hits.update(scan_yfinance_group(HOLDINGS_NSE_STOCKS, "Portfolio — NSE Stocks", LOOKBACK_WEEKS))
    all_hits.update(scan_yfinance_group(HOLDINGS_US_STOCKS, "Portfolio — US Stocks", LOOKBACK_WEEKS))
    all_hits.update(scan_yfinance_group(HOLDINGS_CRYPTO, "Portfolio — Crypto", LOOKBACK_WEEKS))

    message = build_backfill_message(all_hits, LOOKBACK_WEEKS)
    print("\n" + "=" * 60)
    print(message.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))
    print("=" * 60)

    if os.environ.get("SEND_TELEGRAM", "true").lower() == "true":
        send_telegram(message)
    else:
        print("(SEND_TELEGRAM=false — message printed above only, not sent)")


if __name__ == "__main__":
    main()
