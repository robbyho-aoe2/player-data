#!/usr/bin/env python3
"""Computes the "rivalries" section of the console weekly report.

Replaces the old fixed top-5-all-time-games watchlist (which frequently
showed zero games played in a given week's window, since a handful of
long-standing pairs don't necessarily play every week) with a list ranked
by RECENCY: whichever 1v1 opponent pairs have played each other most
recently, so the section is meaningfully different — and actually reflects
real activity — every time this is regenerated.

A pair qualifies as a "rivalry" once they've played at least MIN_GAMES
times (default 3) — filters out one-off matchmaking pairings while still
surfacing genuinely recurring opponents, not just long-tenured ones.

Usage:
    python3 rivalries.py [--min-games N] [--top N] [--recent-games N]

Reads data/console/<profileId>.json (per-player match histories, written
by update_players.py / backfill.yml) and prints a JSON array to stdout —
pipe into a weekly-report file or inspect directly.
"""
import argparse
import glob
import json
from collections import defaultdict


def load_console_pairs(data_dir="data/console"):
    """Returns {(idA, idB) sorted: [match dicts]} for every 1v1 Console
    pair, deduped by matchId (each real match appears in BOTH players'
    files, once from each side)."""
    pairs = defaultdict(list)
    seen_match_ids = defaultdict(set)

    for fn in glob.glob(f"{data_dir}/*.json"):
        with open(fn, encoding="utf-8") as f:
            d = json.load(f)
        pid, name = d["profileId"], d["name"]
        ladder = d.get("ladders", {}).get("1v1 Console", {})
        for m in ladder.get("matches", []):
            opps = m.get("opponents") or []
            if len(opps) != 1:
                continue  # only clean 1v1s — FFA/skipped records excluded
            opp = opps[0]
            oid = opp.get("profileId")
            if oid is None:
                continue
            key = tuple(sorted([pid, oid]))
            mid = m.get("matchId")
            if mid in seen_match_ids[key]:
                continue
            seen_match_ids[key].add(mid)
            pairs[key].append({
                "matchId": mid, "date": m.get("date"), "map": m.get("map"),
                "selfId": pid, "selfName": name,
                "oppId": oid, "oppName": opp.get("name"),
                "selfWon": m.get("won"),
            })
    return pairs


def build_rivalries(pairs, min_games=3, top=10, recent_games=3):
    qualifying = [(k, v) for k, v in pairs.items() if len(v) >= min_games]
    # Sort by most recent game date, descending — "constantly updating":
    # re-running this next week naturally surfaces whoever played most
    # recently THEN, not a fixed list that can go stale for months.
    qualifying.sort(key=lambda kv: max(m["date"] or "" for m in kv[1]), reverse=True)

    out = []
    for key, matches in qualifying[:top]:
        matches.sort(key=lambda m: m["date"] or "", reverse=True)
        m0 = matches[0]
        # Orient nameA/nameB consistently by profileId (matches `key`'s own
        # sort order) so the same pair always reports in the same order
        # week to week, regardless of whose file happened to be read first.
        idA, idB = key
        # Each match's retained record could have been kept from EITHER
        # player's own file (whichever glob() happened to visit first for
        # that matchId) — selfId/oppId aren't reliably A-then-B in a fixed
        # order, so both names and the winner have to be resolved per-match
        # by profileId, not by assuming the retained record's perspective.
        def name_for(pid):
            for m in matches:
                if m["selfId"] == pid:
                    return m["selfName"]
                if m["oppId"] == pid:
                    return m["oppName"]
            return None
        nameA, nameB = name_for(idA), name_for(idB)
        winner_id = lambda m: m["selfId"] if m["selfWon"] else m["oppId"]
        winsA = sum(1 for m in matches if winner_id(m) == idA)
        winsB = len(matches) - winsA
        out.append({
            "nameA": nameA, "idA": idA,
            "nameB": nameB, "idB": idB,
            "allTimeGames": len(matches),
            "record": f"{winsA}-{winsB}",
            "lastPlayed": m0["date"],
            "recentGames": [
                {
                    "date": m["date"], "map": m["map"],
                    "winnerName": (m["selfName"] if m["selfWon"] else m["oppName"]),
                }
                for m in matches[:recent_games]
            ],
        })
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default="data/console")
    ap.add_argument("--min-games", type=int, default=3,
                     help="minimum all-time games together to qualify as a rivalry (default 3)")
    ap.add_argument("--top", type=int, default=10, help="how many rivalries to output (default 10)")
    ap.add_argument("--recent-games", type=int, default=3,
                     help="how many of each rivalry's latest games to include (default 3)")
    args = ap.parse_args()

    pairs = load_console_pairs(args.data_dir)
    rivalries = build_rivalries(pairs, args.min_games, args.top, args.recent_games)
    print(json.dumps(rivalries, indent=2))


if __name__ == "__main__":
    main()
