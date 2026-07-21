#!/usr/bin/env python3
"""
build_aggregate.py — Builds data/aggregate.json from all per-player files.

Run after update_players.py in the GitHub Actions workflow. Reads
data/console/*.json and data/pc/*.json, computes pooled win rates by
civ, map, ELO bracket, and game phase, then writes data/aggregate.json.

Usage:
  python3 build_aggregate.py
  python3 build_aggregate.py --dry-run   # print summary without writing
"""

import argparse
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

# ─── ELO bracket thresholds (console 1v1) ─────────────────────────────────────
# Matches the bracket pills in the civ-lookup tool
ELO_BRACKETS = [
    ("high",   1700, 99999),
    ("himid",  1300, 1699),
    ("lomid",   900, 1299),
    ("low",       0,  899),
]

# ─── Game phase thresholds (seconds) ──────────────────────────────────────────
PHASE_THRESHOLDS = [
    ("early",     0,  1200),   # 0–20 min
    ("mid",    1200,  2400),   # 20–40 min
    ("late",   2400, 99999),   # 40+ min
]

# ─── Ladders that belong to each group ────────────────────────────────────────
CONSOLE_LADDERS = {"1v1 Console", "Team Console"}
PC_LADDERS      = {"1v1 PC", "Team PC"}


def get_bracket(rating):
    if rating is None:
        return None
    for name, lo, hi in ELO_BRACKETS:
        if lo <= rating <= hi:
            return name
    return None


def get_phase(dur):
    if dur is None:
        return None
    for name, lo, hi in PHASE_THRESHOLDS:
        if lo <= dur < hi:
            return name
    return None


def empty_civ_map():
    return defaultdict(lambda: {"games": 0, "wins": 0})


def add_win(d, key, won):
    d[key]["games"] += 1
    if won:
        d[key]["wins"] += 1


def process_group(player_files):
    """
    Aggregate all matches from a list of player JSON files.
    Returns a dict with civWinRates, byMap, byLadder, byEloBracket, byPhase.
    """
    civ_overall  = empty_civ_map()
    by_map       = defaultdict(empty_civ_map)   # map → civ → {games, wins}
    by_ladder    = defaultdict(empty_civ_map)   # ladder → civ → {games, wins}
    by_bracket   = defaultdict(empty_civ_map)   # bracket → civ → {games, wins}
    by_phase     = defaultdict(empty_civ_map)   # phase → civ → {games, wins}

    total_matches = 0
    total_players = 0

    for path in player_files:
        with open(path) as f:
            player = json.load(f)

        total_players += 1

        for ladder_name, ladder_data in player.get("ladders", {}).items():
            # Use latestRating as a proxy for the player's bracket
            rating  = ladder_data.get("meta", {}).get("latestRating")
            bracket = get_bracket(rating)

            for match in ladder_data.get("matches", []):
                civ  = match.get("civ")
                map_ = match.get("map")
                won  = match.get("won")
                dur  = match.get("dur")

                if civ is None or won is None:
                    continue

                total_matches += 1
                phase = get_phase(dur)

                add_win(civ_overall, civ, won)

                if map_:
                    add_win(by_map[map_], civ, won)

                add_win(by_ladder[ladder_name], civ, won)

                if bracket:
                    add_win(by_bracket[bracket], civ, won)

                if phase:
                    add_win(by_phase[phase], civ, won)

    print(f"  players={total_players}, matches={total_matches}")

    return {
        "civWinRates":  dict(civ_overall),
        "byMap":        {m: dict(v) for m, v in by_map.items()},
        "byLadder":     {l: dict(v) for l, v in by_ladder.items()},
        "byEloBracket": {b: dict(v) for b, v in by_bracket.items()},
        "byPhase":      {p: dict(v) for p, v in by_phase.items()},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print summary without writing")
    args = parser.parse_args()

    repo_root = Path(__file__).parent
    data_dir  = repo_root / "data"

    console_files = sorted((data_dir / "console").glob("*.json")) if (data_dir / "console").exists() else []
    pc_files      = sorted((data_dir / "pc").glob("*.json"))      if (data_dir / "pc").exists()      else []

    print(f"=== build_aggregate.py — {date.today()} ===")
    print(f"Console players: {len(console_files)}, PC players: {len(pc_files)}")

    print("\nProcessing console...")
    console_agg = process_group(console_files)

    print("Processing PC...")
    pc_agg = process_group(pc_files)

    aggregate = {
        "lastUpdated":  date.today().isoformat(),
        "playerCounts": {"console": len(console_files), "pc": len(pc_files)},
        "console": console_agg,
        "pc":      pc_agg,
    }

    # Summary
    total_console = sum(v["games"] for v in aggregate["console"]["civWinRates"].values())
    total_pc      = sum(v["games"] for v in aggregate["pc"]["civWinRates"].values())
    print(f"\nConsole total games in aggregate: {total_console:,}")
    print(f"PC total games in aggregate:      {total_pc:,}")
    print(f"Console civs tracked: {len(aggregate['console']['civWinRates'])}")
    print(f"Console maps tracked: {len(aggregate['console']['byMap'])}")

    if args.dry_run:
        print("\nDRY RUN — not writing aggregate.json")
        return

    out_path = data_dir / "aggregate.json"
    with open(out_path, "w") as f:
        json.dump(aggregate, f, indent=2)

    size_kb = out_path.stat().st_size / 1024
    print(f"\nWrote {out_path} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
