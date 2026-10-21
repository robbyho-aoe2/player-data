#!/usr/bin/env python3
"""Computes the "most customs" section of the console weekly report.

Custom games are the lobby games the ranked ladders never see: scrims, Nations Cup sets, team games with friends.
aoe2companion files them under the "unranked" leaderboard, which update_players.py deliberately skips, so
data/console/*.json holds none of them. This script asks the API for them directly.

The matches endpoint takes several profile ids at once and returns the union of their games newest first, so the whole
console roster is queried in chunks of CHUNK ids and each chunk is paged until a page reaches back before the window.
A few hundred requests cover the roster, where one request per player would be thousands.

A game counts when at least two humans are in it (a lobby against the computer is not a custom game between players)
and its date is in the half-open window (start, end], the same convention as weekly_extras.in_window. The record is
wins-losses over games that have a recorded result; a game with no result (disconnect, dropped lobby) still counts
toward "games" but not toward the record.

Usage:
    python3 weekly_customs.py --window-start 2026-10-12 --window-end 2026-10-19 [--top 5] [--players players.json]
Prints a JSON object: {"mostCustoms": [...], "totalCustomGames": N, "playersWithCustoms": N}.
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict

API = "https://data.aoe2companion.com/api/matches"
CHUNK = 100
MAX_PAGES = 60  # safety cap per chunk
RETRIES = 4


def fetch_page(ids, page):
    url = API + "?" + urllib.parse.urlencode({"profile_ids": ",".join(map(str, ids)), "leaderboard_ids": "unranked", "page": page})
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AoE2DataPipeline/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.loads(r.read().decode())
            return d["matches"] if isinstance(d, dict) else d
        except Exception as exc:  # network blips and 429s: back off and retry
            wait = 5 * (attempt + 1)
            print(f"  page {page} failed ({exc}); retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise SystemExit(f"giving up on chunk starting {ids[0]} page {page}")


def in_window(started, start, end):
    d = (started or "")[:10]
    return bool(d) and start < d <= end


def collect(console_ids, start, end):
    ids_set = set(console_ids)
    matches = {}
    ordered = sorted(console_ids)
    for i in range(0, len(ordered), CHUNK):
        chunk = ordered[i:i + CHUNK]
        for page in range(1, MAX_PAGES + 1):
            rows = fetch_page(chunk, page)
            if not rows:
                break
            for m in rows:
                if in_window(m.get("started"), start, end):
                    matches[m["matchId"]] = m
            oldest = min((m.get("started") or "")[:10] for m in rows)
            if len(rows) < 20 or oldest <= start:
                break
            time.sleep(0.3)
        print(f"chunk {i // CHUNK + 1}/{-(-len(ordered) // CHUNK)}: {len(matches)} custom games so far", file=sys.stderr)
        time.sleep(0.3)
    return matches, ids_set


def humans(m):
    return [p for t in m.get("teams", []) for p in t.get("players", []) if p.get("status", "player") == "player" and p.get("profileId") is not None]


def tally(matches, ids_set):
    per = defaultdict(lambda: {"name": None, "games": 0, "wins": 0, "losses": 0})
    kept = 0
    for m in matches.values():
        hs = humans(m)
        if len(hs) < 2:
            continue
        kept += 1
        for p in hs:
            pid = p["profileId"]
            if pid not in ids_set:
                continue
            row = per[pid]
            row["name"] = p.get("name") or row["name"]
            row["games"] += 1
            if p.get("won") is True:
                row["wins"] += 1
            elif p.get("won") is False:
                row["losses"] += 1
    return per, kept


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--players", default="players.json")
    ap.add_argument("--window-start", required=True)
    ap.add_argument("--window-end", required=True)
    ap.add_argument("--top", type=int, default=5)
    args = ap.parse_args()

    roster = json.load(open(args.players, encoding="utf-8"))
    console_ids = [p["profileId"] for p in roster if p.get("group", "console") == "console"]
    matches, ids_set = collect(console_ids, args.window_start, args.window_end)
    per, kept = tally(matches, ids_set)
    ranked = sorted(per.items(), key=lambda kv: (-kv[1]["games"], -kv[1]["wins"], kv[1]["name"] or ""))[:args.top]
    out = {
        "mostCustoms": [
            {"name": r["name"], "profileId": pid, "games": r["games"], "wins": r["wins"], "losses": r["losses"],
             "record": f'{r["wins"]}-{r["losses"]}'}
            for pid, r in ranked
        ],
        "totalCustomGames": kept,
        "playersWithCustoms": len(per),
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
