"""Watch a Copart search and log every bid and status change to CSV.

Each round re-reads the same search results page (one 75 credit call for up
to 100 lots), compares every lot with the previous round and appends one CSV
row per changed field. State lives in a JSON file, so the script can run from
cron with --rounds 1 just as well as in a loop.

    python copart_watch.py "toyota camry" --rounds 3 --every 5
    python copart_watch.py "toyota camry" --only 69970966 73237726 --rounds 1
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone

from copart_client import CopartClient, EmptyPage

WATCHED = ("current_bid", "sale_status", "lane", "item", "yard", "title_group", "odometer", "damage")


def load(path: str) -> dict[str, dict]:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def phase(status: str | None) -> str:
    """'Auction in 4D 23H 46min' ticks down every minute; only the phase matters."""
    return "Scheduled" if (status or "").startswith("Auction in") else (status or "")


def diff(old: dict | None, new: dict) -> list[tuple[str, object, object]]:
    if old is None:
        return [("_first_seen", "", new.get("sale_status") or new.get("current_bid"))]
    out = []
    for f in WATCHED:
        a, b = old.get(f), new.get(f)
        if f == "sale_status" and phase(a) == phase(b):
            continue
        if a != b:
            out.append((f, a, b))
    return out


def one_round(client: CopartClient, args: argparse.Namespace, state: dict[str, dict]) -> int:
    spent = client.credits
    seen: dict[str, dict] = {}
    for lot in client.search(args.query, args.pages, 100):
        if args.only and lot.lot_number not in args.only:
            continue
        seen[str(lot.lot_number)] = asdict(lot)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    changes = []
    for key, snap in seen.items():
        for fld, old, new in diff(state.get(key), snap):
            changes.append([stamp, key, snap["title"], fld, old, new])
        state[key] = snap
    # A lot that drops out of the results was sold, moved or relisted. Log it once.
    for key, snap in state.items():
        if key not in seen and not snap.get("_gone"):
            changes.append([stamp, key, snap["title"], "_left_results", snap.get("sale_status"), ""])
            snap["_gone"] = True

    new_file = not os.path.exists(args.log)
    with open(args.log, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new_file:
            w.writerow(["checked_utc", "lot", "title", "field", "old", "new"])
        w.writerows(changes)
    with open(args.state, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1)

    print(f"{stamp}: {len(seen)} lots, {len(changes)} changes, {client.credits - spent} credits")
    for c in changes[: args.show]:
        print("  " + " | ".join(str(x) for x in c[1:]))
    return len(changes)


def main() -> None:
    ap = argparse.ArgumentParser(description="log Copart bid and status changes for a search")
    ap.add_argument("query")
    ap.add_argument("--pages", type=int, default=1, help="result pages of 100 per round")
    ap.add_argument("--only", type=int, nargs="*", help="keep only these lot numbers")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--every", type=float, default=30, help="minutes between rounds")
    ap.add_argument("--state", default="copart_state.json")
    ap.add_argument("--log", default="copart_changes.csv")
    ap.add_argument("--show", type=int, default=8, help="changes to print per round")
    args = ap.parse_args()
    args.only = set(args.only or [])

    client = CopartClient()
    state = load(args.state)
    for n in range(args.rounds):
        try:
            one_round(client, args, state)
        except EmptyPage as e:
            print(f"round {n + 1} skipped: {e}", file=sys.stderr)
        if n + 1 < args.rounds:
            time.sleep(args.every * 60)
    print(f"total {client.credits} credits", file=sys.stderr)


if __name__ == "__main__":
    main()
