#!/usr/bin/env python3
"""
console_roster.py - Refresh data/console-roster.json (the player list the Console Live page watches) straight from the
console leaderboards, so a brand-new console player shows up with their real rating before our own backfill has a
data file for them.

Row: [profileId, name, platform ('X' | 'P' | ''), 1v1 Console rating, Team Console rating, last 1v1 date, last team date]

Starts from the existing file (built earlier from per-player data files by build_aggregate.py), then overlays every
player on rm_1v1_console / rm_team_console with their current rating, name and last-match date. Players who are on
neither leaderboard are kept only if their last game was within KEEP_DAYS. Platform comes from data/platforms.json
(known for tracked players; blank otherwise).

Usage:  python3 console_roster.py [--dry-run]
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

API = "https://data.aoe2companion.com/api/leaderboards"
HEADERS = {"User-Agent": "AoE2DataPipeline/1.0"}
KEEP_DAYS = 180
REPO = Path(__file__).parent


def fetch_leaderboard(lb_id):
    out, page = [], 1
    while True:
        data = None
        for attempt in range(3):
            try:
                r = requests.get(f"{API}/{lb_id}", params={"page": page, "perPage": 100}, headers=HEADERS, timeout=25)
                if r.status_code == 429:
                    time.sleep([10, 30, 60][attempt])
                    continue
                r.raise_for_status()
                data = r.json()
                break
            except Exception as e:
                print(f"  [error] {lb_id} page {page}: {e}")
        if not data or not data.get("players"):
            break
        out.extend(data["players"])
        if len(out) >= data.get("total", len(out)):
            break
        page += 1
        time.sleep(0.3)
    return out


def merge(old_rows, lb1, lbt, plat_map, today):
    """Pure merge: returns the new sorted row list. Rows are [id, name, plat, r1, rt, last1, lastT]."""
    cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
    rows = {r[0]: list(r) for r in old_rows}
    on_board = set()

    def day(p):
        t = p.get("lastMatchTime") or ""
        return t[:10]

    for p in lb1:
        pid = p["profileId"]
        on_board.add(pid)
        r = rows.setdefault(pid, [pid, p.get("name") or str(pid), "", 0, 0, "", ""])
        r[1] = p.get("name") or r[1]
        r[3] = p.get("rating") or r[3]
        r[5] = day(p) or r[5]
    for p in lbt:
        pid = p["profileId"]
        on_board.add(pid)
        r = rows.setdefault(pid, [pid, p.get("name") or str(pid), "", 0, 0, "", ""])
        r[1] = p.get("name") or r[1]
        r[4] = p.get("rating") or r[4]
        r[6] = day(p) or r[6]
    letter = {"Xbox": "X", "PSN": "P"}
    for pid, r in rows.items():
        if not r[2]:
            r[2] = letter.get(plat_map.get(str(pid)), "")
    keep = [r for pid, r in rows.items() if pid in on_board or max(r[5], r[6]) >= cutoff]
    keep.sort(key=lambda r: -max(r[3], r[4]))
    return keep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path = REPO / "data" / "console-roster.json"
    old = json.loads(path.read_text(encoding="utf-8")).get("players", []) if path.exists() else []
    plat_path = REPO / "data" / "platforms.json"
    plat_map = json.loads(plat_path.read_text(encoding="utf-8")) if plat_path.exists() else {}

    lb1, lbt = fetch_leaderboard("rm_1v1_console"), fetch_leaderboard("rm_team_console")
    print(f"Leaderboards: {len(lb1)} on rm_1v1_console, {len(lbt)} on rm_team_console; existing roster rows: {len(old)}")
    if len(lb1) < 500 or len(lbt) < 500:   # a partial fetch must never shrink the file
        print("Leaderboard fetch looks incomplete; leaving the roster unchanged.")
        return 0
    rows = merge(old, lb1, lbt, plat_map, datetime.now(timezone.utc).date())
    ids_old = {r[0] for r in old}
    print(f"New roster: {len(rows)} players ({len({r[0] for r in rows} - ids_old)} new, {len(ids_old - {r[0] for r in rows})} dropped as inactive)")
    if args.dry_run:
        print("[dry-run] nothing written")
        return 0
    out = {"generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "cols": ["id", "name", "plat", "r1v1", "rTeam", "last1v1", "lastTeam"], "players": rows}
    new_text = json.dumps(out, separators=(",", ":"), ensure_ascii=False)
    path.write_text(new_text, encoding="utf-8")
    print(f"Wrote {path} ({len(new_text) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
