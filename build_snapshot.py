#!/usr/bin/env python3
"""Writes data/snapshots/console-<date>.json — a point-in-time capture of
every console player's rating/game counts, used by weekly_extras.py to
diff week-over-week (elo1v1Change, eloTeamChange, ratingChangePC1v1) and
to correctly restate a PAST report's "finishing Elo" later without it
drifting to whatever the player is rated on the day of the restatement
(see compute_most_games/compute_biggest_upsets' snapshot_end_ratings).

No prior version of this step was ever committed as a script — it had
been done ad hoc each week, which is how the 2026-09-14 snapshot ended up
silently missing ratingTeam entirely (compute_team_rating_snapshot's
output just never got merged in that week). Running this script is now
the one way a snapshot gets written, so every week's snapshot has the
same fields going forward.

Usage:
    python3 build_snapshot.py --date 2026-09-21 \
        --window-start 2026-09-14 --window-end 2026-09-21 \
        --out data/snapshots/console-2026-09-21.json

`--window-start`/`--window-end` are only used for civStatsThisWindow
(per-civ games/wins for that week alone); the player ratings themselves
are always each player's CURRENT live values, since this script is meant
to be run same-day as the week's own report.
"""
import argparse
import glob
import json
from collections import defaultdict
from pathlib import Path

from weekly_extras import in_window, is_void_match, load_players


def build_players(players):
    out = {}
    for p in players:
        pid = p.get("profileId")
        ladders = p.get("ladders", {})
        m1v1 = ladders.get("1v1 Console", {}).get("meta", {})
        mteam = ladders.get("Team Console", {}).get("meta", {})
        mpc1v1 = ladders.get("1v1 PC", {}).get("meta", {})
        row = {
            "name": p.get("name"),
            "games1v1": m1v1.get("totalGames", 0),
            "gamesTeam": mteam.get("totalGames", 0),
            "rating1v1": m1v1.get("latestRating"),
            "peakRating1v1": m1v1.get("peakRating"),
            "ratingTeam": mteam.get("latestRating"),
        }
        if mpc1v1.get("latestRating") is not None:
            row["gamesPC1v1"] = mpc1v1.get("totalGames", 0)
            row["ratingPC1v1"] = mpc1v1.get("latestRating")
            row["peakRatingPC1v1"] = mpc1v1.get("peakRating")
        out[str(pid)] = row
    return out


def build_civ_stats(players, start, end):
    counts = defaultdict(lambda: {"games": 0, "wins": 0})
    for p in players:
        for ladder in ("1v1 Console", "Team Console"):
            for m in p.get("ladders", {}).get(ladder, {}).get("matches", []):
                if not in_window(m.get("date"), start, end) or is_void_match(m):
                    continue
                civ = m.get("civ")
                if not civ:
                    continue
                counts[civ]["games"] += 1
                if m.get("won"):
                    counts[civ]["wins"] += 1
    return dict(counts)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default="data/console")
    ap.add_argument("--date", required=True, help="This snapshot's own date, YYYY-MM-DD.")
    ap.add_argument("--window-start", required=True, help="Start of this week's window, for civStatsThisWindow.")
    ap.add_argument("--window-end", required=True, help="End of this week's window, for civStatsThisWindow.")
    ap.add_argument("--out", required=True, help="Output path, e.g. data/snapshots/console-<date>.json")
    args = ap.parse_args()

    players = list(load_players(args.data_dir))
    snapshot_players = build_players(players)

    out = {
        "date": args.date,
        "rosterCount": len(snapshot_players),
        "players": snapshot_players,
        "civStatsThisWindow": build_civ_stats(players, args.window_start, args.window_end),
    }

    out_path = Path(args.out)
    out_path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"Wrote {out_path}: {len(snapshot_players)} players")


if __name__ == "__main__":
    main()
