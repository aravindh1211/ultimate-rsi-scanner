"""
One-off retrospective check — Ultimate RSI Scanner
Run manually: `python scripts/backfill_4weeks.py`

Replays ALL FOUR live signals across each of the last N closed weekly
bars, not just the latest one — so you can see what WOULD have fired
each of the past N Fridays, even though the bot only started running
now (or to sanity-check a recent live run).

Signals replayed, identical logic to src/scanner.py:
  🟢 Entry        — URSI crosses above signal, having been <50 prior bar
  💎 Deep Accum   — flag on an entry hit if URSI dipped <20 in the ~8
                     bars before that crossover
  🔻 Trim Warning — URSI was ≥80 prior bar, falls below 80 this bar
  👀 Approaching  — URSI <50, rising, gap to signal narrowing at a rate
                     that projects a cross within ~6 weeks (skipped for
                     a bar that already fired an Entry that same week)

At each bar in the lookback window, only data available up to and
including that bar is used — no lookahead.

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
    INDIAN_INDICES, US_INDICES,
    HOLDINGS_NSE_STOCKS, HOLDINGS_US_STOCKS, HOLDINGS_CRYPTO,
    URSI_LENGTH, URSI_SMOOTH, URSI_MIDLINE,
    URSI_TRIM_LEVEL, URSI_DEEP_ACCUM_LEVEL, URSI_DEEP_ACCUM_LOOKBACK,
    URSI_APPROACH_MAX_WEEKS, URSI_APPROACH_LOOKBACK,
    calc_ultimate_rsi, get_label, send_telegram,
    YF_PERIOD, YF_INTERVAL,
)
import pandas as pd  # noqa: E402

LOOKBACK_WEEKS = int(os.environ.get("LOOKBACK_WEEKS", "4"))


def find_triggers_over_lookback(close: pd.Series, weeks: int) -> dict:
    """
    Replays all four signals across each of the last `weeks` closed bars,
    treating each bar in turn as if it were "today" — using only data
    available up to and including that bar (no lookahead).

    Returns {"entry": [...], "trim": [...], "approaching": [...]} — each
    a list of per-bar hit dicts (possibly empty).
    """
    arsi, signal = calc_ultimate_rsi(close)
    empty = {"entry": [], "trim": [], "approaching": []}
    if arsi is None:
        return empty

    valid = (~arsi.isna()) & (~signal.isna())
    arsi_v, signal_v = arsi[valid], signal[valid]
    min_needed = max(weeks + 1, URSI_DEEP_ACCUM_LOOKBACK + 1, URSI_APPROACH_LOOKBACK + 1)
    if len(arsi_v) < min_needed:
        return empty

    entry_hits, trim_hits, approaching_hits = [], [], []

    for i in range(-weeks, 0):
        date_label = arsi_v.index[i].strftime("%d %b %Y")
        curr_arsi,   prev_arsi   = float(arsi_v.iloc[i]),   float(arsi_v.iloc[i - 1])
        curr_signal, prev_signal = float(signal_v.iloc[i]), float(signal_v.iloc[i - 1])

        # ── 🟢 Entry (+ 💎 Deep Accum) ────────────────────────────────
        crossed_up   = (prev_arsi <= prev_signal) and (curr_arsi > curr_signal)
        was_below_50 = prev_arsi < URSI_MIDLINE
        entry_fired = False
        if crossed_up and was_below_50:
            entry_fired = True
            # Look back URSI_DEEP_ACCUM_LOOKBACK bars before this crossover
            # bar (exclusive of it) for a dip under 20.
            start = i - URSI_DEEP_ACCUM_LOOKBACK
            end = i  # exclusive
            lookback_window = arsi_v.iloc[start:end] if start >= -len(arsi_v) else arsi_v.iloc[:end]
            deep_accum = bool((lookback_window < URSI_DEEP_ACCUM_LEVEL).any()) if len(lookback_window) else False
            deep_accum_low = round(float(lookback_window.min()), 2) if len(lookback_window) else None
            entry_hits.append({
                "date": date_label,
                "ursi": round(curr_arsi, 2),
                "signal": round(curr_signal, 2),
                "prev_ursi": round(prev_arsi, 2),
                "deep_accum": deep_accum,
                "deep_accum_low": deep_accum_low,
            })

        # ── 🔻 Trim Warning ────────────────────────────────────────────
        if prev_arsi >= URSI_TRIM_LEVEL and curr_arsi < URSI_TRIM_LEVEL:
            trim_hits.append({
                "date": date_label,
                "ursi": round(curr_arsi, 2),
                "prev_ursi": round(prev_arsi, 2),
            })

        # ── 👀 Approaching Crossover (skip if Entry already fired here) ─
        if not entry_fired and curr_arsi < URSI_MIDLINE and curr_arsi < curr_signal:
            n = URSI_APPROACH_LOOKBACK + 1
            window_start = i - URSI_APPROACH_LOOKBACK
            if abs(window_start) <= len(arsi_v):
                recent_arsi   = arsi_v.iloc[window_start:i + 1] if i != -1 else arsi_v.iloc[window_start:]
                recent_signal = signal_v.iloc[window_start:i + 1] if i != -1 else signal_v.iloc[window_start:]
                if len(recent_arsi) == n:
                    gaps = (recent_signal - recent_arsi).tolist()  # oldest → newest
                    weekly_narrowing = [gaps[j] - gaps[j + 1] for j in range(len(gaps) - 1)]
                    avg_rate = sum(weekly_narrowing) / len(weekly_narrowing)
                    rising = curr_arsi > float(recent_arsi.iloc[0])
                    if avg_rate > 0 and rising:
                        curr_gap = gaps[-1]
                        est_weeks = curr_gap / avg_rate
                        if 0 < est_weeks <= URSI_APPROACH_MAX_WEEKS:
                            approaching_hits.append({
                                "date": date_label,
                                "ursi": round(curr_arsi, 2),
                                "signal": round(curr_signal, 2),
                                "gap": round(curr_gap, 2),
                                "est_weeks": round(est_weeks, 1),
                            })

    return {"entry": entry_hits, "trim": trim_hits, "approaching": approaching_hits}


def scan_yfinance_group(tickers: list, label: str, weeks: int) -> dict:
    """
    Returns {ticker: {"entry": [...], "trim": [...], "approaching": [...]}}
    for tickers that had at least one hit of any kind in the lookback.
    """
    results = {}
    print(f"── {label}: {len(tickers)} tickers")
    for ticker in tickers:
        try:
            df = yf.Ticker(ticker).history(period=YF_PERIOD, interval=YF_INTERVAL, auto_adjust=True)
            min_bars = URSI_LENGTH + URSI_SMOOTH + max(weeks, URSI_DEEP_ACCUM_LOOKBACK, URSI_APPROACH_LOOKBACK) + 5
            if df is None or df.empty or len(df) < min_bars:
                print(f"  ⚠ {ticker}: insufficient data")
                continue
            close = df["Close"].squeeze().dropna()
            hits = find_triggers_over_lookback(close, weeks)
            total_hits = len(hits["entry"]) + len(hits["trim"]) + len(hits["approaching"])
            if total_hits:
                results[ticker] = hits
                print(f"  🔔 {ticker}: {len(hits['entry'])} entry, {len(hits['trim'])} trim, "
                      f"{len(hits['approaching'])} approaching in lookback")
        except Exception as e:
            print(f"  ✗ {ticker}: {e}")
    return results


def build_backfill_message(all_hits: dict, weeks: int) -> str:
    now = datetime.utcnow().strftime("%d %b %Y")
    lines = [
        f"🕰️ <b>Ultimate RSI — {weeks}-Week Retrospective Check</b>  |  run on {now} (UTC)",
        "One-time lookback — checks what WOULD have triggered (entry, trim, and "
        f"approaching-crossover) on each of the last {weeks} closed weekly bars.",
        "",
    ]

    if not all_hits:
        lines.append(f"✅ <b>No triggers found in the last {weeks} weeks</b> across any tracked instrument.")
        return "\n".join(lines)

    # Flatten into per-date, per-signal-type buckets for a chronological view
    by_date: dict = {}   # date -> {"entry": [(sym, hit)], "trim": [...], "approaching": [...]}
    for sym, kinds in all_hits.items():
        for kind in ("entry", "trim", "approaching"):
            for h in kinds[kind]:
                by_date.setdefault(h["date"], {"entry": [], "trim": [], "approaching": []})
                by_date[h["date"]][kind].append((sym, h))

    for date in sorted(by_date.keys(), key=lambda d: datetime.strptime(d, "%d %b %Y")):
        lines.append(f"<b>Week of {date}</b>")
        day = by_date[date]

        for sym, h in sorted(day["trim"], key=lambda x: -x[1]["prev_ursi"]):
            lines.append(f"  🔻 {get_label(sym)}")

        for sym, h in sorted(day["entry"], key=lambda x: x[1]["ursi"]):
            marker = "💎" if h.get("deep_accum") else "🟢"
            lines.append(f"  {marker} {get_label(sym)}")

        for sym, h in sorted(day["approaching"], key=lambda x: x[1]["est_weeks"]):
            lines.append(f"  👀 {get_label(sym)}")

        lines.append("")

    lines += [
        "─────────────────────────",
        "🟢 Entry  💎 Deep Accum  🔻 Trim Warning  👀 Approaching Crossover — same definitions as the live weekly alert.",
        "💡 <i>Retrospective only — not a live signal. Capital Saturation not checked here.</i>",
    ]
    return "\n".join(lines)


def main():
    print(f"Running {LOOKBACK_WEEKS}-week retrospective backfill (all 4 signals)...")

    all_hits: dict = {}
    all_hits.update(scan_yfinance_group(INDIAN_INDICES, "Indian Indices", LOOKBACK_WEEKS))
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
