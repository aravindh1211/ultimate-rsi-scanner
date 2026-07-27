"""
Small helper used by .github/workflows/update-holdings.yml.

Reads comma-separated ticker strings from environment variables (one per
holdings category) and merges them into holdings.json. Any category left
blank is untouched — you only need to pass the category you're changing.

Set MODE=replace to replace a category outright, or MODE=add (default) to
add tickers to the existing list without removing anything.
Prefix a ticker with "-" to remove it, e.g. "-IRFC.NS" removes IRFC from
the NSE list even when MODE=add.
"""

import os
import json

HOLDINGS_FILE = "holdings.json"


def parse_list(raw: str) -> list:
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


def merge(existing: list, incoming: list, mode: str) -> list:
    if mode == "replace":
        return [t for t in incoming if not t.startswith("-")]

    result = list(existing)
    for t in incoming:
        if t.startswith("-"):
            ticker = t[1:].strip()
            result = [x for x in result if x != ticker]
        elif t not in result:
            result.append(t)
    return result


def main():
    mode = os.environ.get("MODE", "add").strip().lower()

    with open(HOLDINGS_FILE, "r") as f:
        holdings = json.load(f)

    changes = {
        "nse_stocks": os.environ.get("NSE_STOCKS", ""),
        "us_stocks": os.environ.get("US_STOCKS", ""),
        "crypto": os.environ.get("CRYPTO", ""),
    }

    changed = False
    for key, raw in changes.items():
        incoming = parse_list(raw)
        if not incoming:
            continue
        new_list = merge(holdings.get(key, []), incoming, mode)
        if new_list != holdings.get(key, []):
            holdings[key] = new_list
            changed = True
            print(f"Updated {key}: {new_list}")

    if not changed:
        print("No changes to apply — all category inputs were blank or no-ops.")
        return

    with open(HOLDINGS_FILE, "w") as f:
        json.dump(holdings, f, indent=2, sort_keys=True)
    print("holdings.json updated.")


if __name__ == "__main__":
    main()
