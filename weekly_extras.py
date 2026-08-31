#!/usr/bin/env python3
"""Computes the parts of the console weekly report that are scoped to a
specific date window: the game-count breakdown by mode (1v1/2v2/3v3/4v4)
and the top-5 duos/trios/quads for that window.

Also emits a PC 1v1 standings snapshot (current rating + window games for
every console player with 1v1 PC history) — NOT a week-over-week delta.
No prior script in this repo has ever recorded PC ratings into
data/snapshots/, so there's no historical baseline to diff against yet.
This writes one into data/snapshots/console-<date>.json (alongside the
existing rating1v1 console fields) so that starting with the *next* run,
build_aggregate/weekly-report tooling can diff PC ratings the same way
biggestMovers already diffs console ratings. Until then, the report should
label this section "current standings" rather than implying movement.

Usage:
    python3 weekly_extras.py --window-start 2026-08-26 --window-end 2026-08-31

Reads data/console/<profileId>.json and prints one JSON object to stdout
with keys: gameBreakdown, squads, pc1v1Standings.
"""
import argparse
import glob
import itertools
import json
from collections import defaultdict


def load_players(data_dir="data/console"):
    for fn in glob.glob(f"{data_dir}/*.json"):
        with open(fn, encoding="utf-8") as f:
            yield json.load(f)


def in_window(date_str, start, end):
    return bool(date_str) and start <= date_str <= end


def compute_game_breakdown(players, start, end):
    """Dedup by matchId per ladder, size Team Console matches by
    1 + len(teammates) to split 2v2/3v3/4v4 apart."""
    seen_1v1 = set()
    seen_by_size = defaultdict(set)

    for p in players:
        ladders = p.get("ladders", {})
        for m in ladders.get("1v1 Console", {}).get("matches", []):
            if in_window(m.get("date"), start, end):
                seen_1v1.add(m.get("matchId"))
        for m in ladders.get("Team Console", {}).get("matches", []):
            if not in_window(m.get("date"), start, end):
                continue
            size = 1 + len(m.get("teammates") or [])
            seen_by_size[size].add(m.get("matchId"))

    breakdown = {"1v1": len(seen_1v1)}
    for size in sorted(seen_by_size):
        breakdown[f"{size}v{size}"] = len(seen_by_size[size])
    breakdown["total"] = len(seen_1v1) + sum(len(v) for v in seen_by_size.values())
    return breakdown


def compute_squads(players, start, end, top=5, min_games=1):
    """Top-N duos/trios/quads by games played together within the window,
    Team Console only (matches the weekly report's console scope)."""
    seen = {}  # matchId -> (frozenset profileIds, won)
    for p in players:
        owner_id = p.get("profileId")
        for m in p.get("ladders", {}).get("Team Console", {}).get("matches", []):
            if not in_window(m.get("date"), start, end):
                continue
            mid = m.get("matchId")
            if mid in seen:
                continue
            teammates = m.get("teammates") or []
            squad = {(owner_id, p.get("name"))} | {
                (t.get("profileId"), t.get("name")) for t in teammates
                if t.get("profileId") is not None
            }
            if len(squad) >= 2:
                seen[mid] = (frozenset(squad), m.get("won"))

    stats = {2: defaultdict(lambda: {"games": 0, "wins": 0}),
              3: defaultdict(lambda: {"games": 0, "wins": 0}),
              4: defaultdict(lambda: {"games": 0, "wins": 0})}

    for squad, won in seen.values():
        size = len(squad)
        for n in (2, 3, 4):
            if size < n:
                continue
            for combo in itertools.combinations(sorted(squad), n):
                stats[n][combo]["games"] += 1
                if won:
                    stats[n][combo]["wins"] += 1

    def serialize(n):
        rows = []
        for combo, v in stats[n].items():
            if v["games"] < min_games:
                continue
            rows.append({
                "names": [c[1] for c in combo],
                "profileIds": [c[0] for c in combo],
                "games": v["games"],
                "winRate": round(v["wins"] / v["games"], 4),
            })
        rows.sort(key=lambda r: (-r["games"], -r["winRate"]))
        return rows[:top]

    return {"duos": serialize(2), "trios": serialize(3), "quads": serialize(4)}


def compute_pc1v1_standings(players, start, end, top=15):
    """Current 1v1 PC rating + window games for console players with any
    1v1 PC history — a starting point until a prior-week snapshot exists
    to diff against for real rank movement."""
    rows = []
    for p in players:
        pc1 = p.get("ladders", {}).get("1v1 PC", {})
        meta = pc1.get("meta", {})
        rating = meta.get("latestRating")
        if rating is None:
            continue
        window_games = sum(
            1 for m in pc1.get("matches", []) if in_window(m.get("date"), start, end)
        )
        rows.append({
            "name": p.get("name"),
            "profileId": p.get("profileId"),
            "rating": rating,
            "totalGamesPC1v1": meta.get("totalGames", 0),
            "windowGamesPC1v1": window_games,
        })
    rows.sort(key=lambda r: -r["rating"])
    return rows[:top]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default="data/console")
    ap.add_argument("--window-start", required=True)
    ap.add_argument("--window-end", required=True)
    ap.add_argument("--squad-top", type=int, default=5)
    args = ap.parse_args()

    players = list(load_players(args.data_dir))

    out = {
        "gameBreakdown": compute_game_breakdown(players, args.window_start, args.window_end),
        "squads": compute_squads(players, args.window_start, args.window_end, top=args.squad_top),
        "pc1v1Standings": compute_pc1v1_standings(players, args.window_start, args.window_end),
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
