"""
Monthly Holdings Digest — GitHub Actions companion script

Runs daily from the 28th to the 31st of every month (see
.github/workflows/monthly_digest.yml). On every run except the actual last
calendar day of the month, it does nothing and exits quietly. On the last
calendar day, it reads state/weekly_log.json — which src/scanner.py appends
to after every scheduled Friday run — and sends a single Telegram message
compiling every weekly holdings/indices notification sent that month.
Afterwards it clears the log so next month starts fresh.

This digest is a recap of the weekly Ultimate RSI cross notifications
about your indices + portfolio holdings (src/scanner.py).
"""

import os
import json
import logging
from datetime import datetime, timedelta

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
WEEKLY_LOG_FILE    = os.environ.get("WEEKLY_LOG_FILE", "state/weekly_log.json")

# Labels come from the same lookup tables scanner.py uses, so names in the
# digest match what you see in the weekly messages.
from scanner import get_label  # noqa: E402  (import after path setup below)


def is_last_day_of_month(today: datetime) -> bool:
    tomorrow = today + timedelta(days=1)
    return tomorrow.month != today.month


def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("❌ Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
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


def load_weekly_log() -> list:
    if not os.path.exists(WEEKLY_LOG_FILE):
        return []
    try:
        with open(WEEKLY_LOG_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        log.error(f"✗ Failed to load weekly log: {e}")
        return []


def clear_weekly_log() -> None:
    os.makedirs(os.path.dirname(WEEKLY_LOG_FILE) or ".", exist_ok=True)
    with open(WEEKLY_LOG_FILE, "w") as f:
        json.dump([], f)


def build_digest(entries: list, month_label: str) -> str:
    lines = [
        f"🗓️ <b>Monthly Ultimate RSI Digest — {month_label}</b>",
        f"Compiling {len(entries)} weekly notification(s) sent this month",
        "",
    ]

    if not entries:
        lines.append("No weekly notifications were logged this month.")
        return "\n".join(lines)

    any_trigger_all_month = False
    any_trim_all_month = False
    any_approaching_all_month = False
    all_hit_symbols: dict = {}          # sym -> list of date labels (entry signals)
    all_trim_symbols: dict = {}         # sym -> list of date labels (trim warnings)
    all_approaching_symbols: dict = {}  # sym -> list of date labels (approaching-crossover notes)

    for entry in entries:
        date_label = datetime.strptime(entry["date"], "%Y-%m-%d").strftime("%d %b")
        triggered = entry.get("triggered", {})
        trim_triggered = entry.get("trim_triggered", {})              # absent in pre-trim-feature log entries
        approaching_triggered = entry.get("approaching_triggered", {})  # absent in pre-approaching-feature log entries

        lines.append(f"<b>Week of {date_label}</b>  (scanned {entry.get('total_scanned', '?')})")

        if trim_triggered:
            any_trim_all_month = True
            for sym, info in sorted(trim_triggered.items(), key=lambda x: -x[1].get("prev_ursi", 0)):
                lines.append(
                    f"  🔻 {get_label(sym)}  →  URSI {info.get('prev_ursi', '?')} → "
                    f"<b>{info.get('ursi', '?')}</b>  (trim warning)"
                )
                all_trim_symbols.setdefault(sym, []).append(date_label)

        if triggered:
            any_trigger_all_month = True
            for sym, info in sorted(triggered.items(), key=lambda x: x[1].get("ursi", 0)):
                lines.append(
                    f"  🟢 {get_label(sym)}  →  URSI {info.get('prev_ursi', '?')} → "
                    f"<b>{info.get('ursi', '?')}</b> (signal {info.get('signal', '?')})"
                )
                all_hit_symbols.setdefault(sym, []).append(date_label)

        if approaching_triggered:
            any_approaching_all_month = True
            for sym, info in sorted(approaching_triggered.items(), key=lambda x: x[1].get("est_weeks", 99)):
                lines.append(
                    f"  👀 {get_label(sym)}  →  URSI {info.get('ursi', '?')} vs signal {info.get('signal', '?')} "
                    f"(~{info.get('est_weeks', '?')}w to projected cross)"
                )
                all_approaching_symbols.setdefault(sym, []).append(date_label)

        if not triggered and not trim_triggered and not approaching_triggered:
            lines.append("  ✅ No triggers")
        lines.append("")

    if any_trim_all_month:
        lines.append("<b>🔻 Consolidated — trim warnings this month:</b>")
        for sym, dates in sorted(all_trim_symbols.items()):
            times = f" (x{len(dates)})" if len(dates) > 1 else ""
            lines.append(f"  • {get_label(sym)} — {', '.join(dates)}{times}")
        lines.append("")

    if any_trigger_all_month:
        lines.append("<b>📋 Consolidated — symbols that crossed this month:</b>")
        for sym, dates in sorted(all_hit_symbols.items()):
            times = f" (x{len(dates)})" if len(dates) > 1 else ""
            lines.append(f"  • {get_label(sym)} — {', '.join(dates)}{times}")
        lines.append("")

    if any_approaching_all_month:
        lines.append("<b>👀 Consolidated — approaching crossover this month:</b>")
        lines.append("<i>Symbols flagged as \"get ready\" — check whether they've actually crossed by now.</i>")
        for sym, dates in sorted(all_approaching_symbols.items()):
            times = f" (x{len(dates)})" if len(dates) > 1 else ""
            lines.append(f"  • {get_label(sym)} — {', '.join(dates)}{times}")
        lines.append("")

    lines.append("─────────────────────────")
    if any_trigger_all_month or any_trim_all_month or any_approaching_all_month:
        lines.append("💡 <i>Recap only — refer to the weekly messages above for context on each hit. "
                      "Capital Saturation not checked here — verify separately before allocating.</i>")
    else:
        lines.append("✅ <b>No entry triggers, trim warnings, or approaching-crossover notes on holdings/indices all month.</b>")

    return "\n".join(lines)


def main():
    today = datetime.utcnow()

    if not is_last_day_of_month(today):
        log.info(f"Not the last day of the month ({today.date()}) — skipping digest.")
        return

    log.info("📅 Last day of the month — compiling monthly holdings digest")
    entries = load_weekly_log()
    month_label = today.strftime("%B %Y")

    send_telegram(build_digest(entries, month_label))
    clear_weekly_log()
    log.info("✅ Digest sent, weekly log cleared for next month.")


if __name__ == "__main__":
    main()
