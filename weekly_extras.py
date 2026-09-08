#!/usr/bin/env python3
"""Computes the parts of the console weekly report that are scoped to a
specific date window: the game-count breakdown by mode (1v1/2v2/3v3/4v4),
the top-5 duos/trios/quads for that window, most-active players (with
finishing Elo + net change), the biggest upsets, and civ popularity.

Also emits a PC 1v1 standings snapshot (current rating + window games for
every console player with 1v1 PC history) — NOT a week-over-week delta.
No prior script in this repo has ever recorded PC ratings into
data/snapshots/, so there's no historical baseline to diff against yet.
This writes one into data/snapshots/console-<date>.json (alongside the
existing rating1v1 console fields) so that starting with the *next* run,
build_aggregate/weekly-report tooling can diff PC ratings the same way
biggestMovers already diffs console ratings. Until then, the report should
label this section "current standings" rather than implying movement.

Team Console rating has the same gap: data/snapshots/console-<date>.json
has only ever recorded `rating1v1` per player, never a Team-ladder rating,
so `compute_most_games`'s `eloTeamChange` is always None today. Starting
whatever week the external report-writer begins merging this script's
`teamRatingSnapshot` output into that day's data/snapshots/console-<date>.json
(as `ratingTeam` per player, next to the existing `rating1v1`), the
*following* week's report can diff it the same way `elo1v1Change` already
works below. Until two consecutive snapshots carry `ratingTeam`, leave
eloTeamChange as null rather than guessing.

Void/disconnected matches: a match where a player's own record has
`"won": null` (and typically `"dur": null`) has no recorded outcome —
confirmed by cross-checking the opponent's copy of the same matchId, which
in every sampled case had a real `won`/`dur` while this side had neither.
These are not draws or in-progress games, they're one side's client never
reporting a result (e.g. a disconnect) — Python truthiness silently treats
`None` as `False` if you don't check for it explicitly, which previously
fabricated fake "upsets" out of games that never actually resolved. Always
skip these with `is_void_match()` before trusting a match's `won`/`dur`.

Usage:
    python3 weekly_extras.py --window-start 2026-08-26 --window-end 2026-08-31

Reads data/console/<profileId>.json and prints one JSON object to stdout
with keys: gameBreakdown, squads, pc1v1Standings, mostGames, biggestUpsets,
civPopularity, teamRatingSnapshot.
"""
import argparse
import glob
import itertools
import json
from collections import defaultdict


def is_void_match(m):
    """True if this side's copy of the match has no recorded outcome —
    a disconnect/never-finished game, not a real result. See module
    docstring. Callers should skip these before reading `won` or `dur`."""
    return m.get("won") is None


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


def compute_squads(players, start, end, top=5, min_games=1, roster_ids=None):
    """Top-N duos/trios/quads by games played together within the window,
    Team Console only (matches the weekly report's console scope).

    `roster_ids`, when given, restricts squad membership to teammates in
    that set — same convention as snl_report.py's ratstacks, so an
    untracked teammate doesn't get folded into a squad we can't verify.
    Pass the full console profileId set here to match the SNL report's
    behavior; omit (None) for the old any-teammate behavior."""
    seen = {}  # matchId -> (frozenset profileIds, won)
    for p in players:
        owner_id = p.get("profileId")
        for m in p.get("ladders", {}).get("Team Console", {}).get("matches", []):
            if not in_window(m.get("date"), start, end) or is_void_match(m):
                continue
            mid = m.get("matchId")
            if mid in seen:
                continue
            teammates = m.get("teammates") or []
            squad = {(owner_id, p.get("name"))} | {
                (t.get("profileId"), t.get("name")) for t in teammates
                if t.get("profileId") is not None
                and (roster_ids is None or t.get("profileId") in roster_ids)
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


def compute_team_rating_snapshot(players):
    """{profileId: ratingTeam} for every console player with a Team Console
    rating right now. Merge this into today's data/snapshots/console-<date>.json
    as `ratingTeam` (next to the existing `rating1v1`) so a *future* week's
    report can diff it — see the Team-rating gap noted in the module
    docstring. Not itself a week-over-week delta."""
    out = {}
    for p in players:
        meta = p.get("ladders", {}).get("Team Console", {}).get("meta", {})
        rating = meta.get("latestRating")
        if rating is not None:
            out[p.get("profileId")] = rating
    return out


def compute_most_games(players, start, end, snapshot_start_ratings=None, top=10):
    """Top-N most-active players this window (1v1 + Team Console combined),
    with finishing (current) Elo and net change vs. the window-start
    snapshot, per ladder.

    `snapshot_start_ratings` is the `players` dict loaded from
    data/snapshots/console-<windowStart>.json, e.g.
    `{"13623295": {"rating1v1": 1878, ...}}` — pass None to leave every
    *Change field null (bootstrap week / no snapshot found). `ratingTeam`
    in that dict is optional and only present once compute_team_rating_snapshot
    output has been merged into snapshots for at least one prior week."""
    snapshot_start_ratings = snapshot_start_ratings or {}
    stats = defaultdict(lambda: {"games": 0, "wins": 0, "name": None})
    for p in players:
        pid = p.get("profileId")
        name = p.get("name")
        for ladder in ("1v1 Console", "Team Console"):
            for m in p.get("ladders", {}).get(ladder, {}).get("matches", []):
                if not in_window(m.get("date"), start, end) or is_void_match(m):
                    continue
                stats[pid]["name"] = name
                stats[pid]["games"] += 1
                if m.get("won"):
                    stats[pid]["wins"] += 1

    by_pid = {p.get("profileId"): p for p in players}
    rows = []
    for pid, v in stats.items():
        if v["games"] == 0:
            continue
        p = by_pid.get(pid, {})
        elo1v1 = p.get("ladders", {}).get("1v1 Console", {}).get("meta", {}).get("latestRating")
        eloTeam = p.get("ladders", {}).get("Team Console", {}).get("meta", {}).get("latestRating")
        start_ratings = snapshot_start_ratings.get(str(pid)) or {}
        start_1v1 = start_ratings.get("rating1v1")
        start_team = start_ratings.get("ratingTeam")
        rows.append({
            "name": v["name"],
            "profileId": pid,
            "games": v["games"],
            "winRate": round(v["wins"] / v["games"], 4),
            "elo1v1": elo1v1,
            "elo1v1Change": (elo1v1 - start_1v1) if (elo1v1 is not None and start_1v1 is not None) else None,
            "eloTeam": eloTeam,
            "eloTeamChange": (eloTeam - start_team) if (eloTeam is not None and start_team is not None) else None,
        })
    rows.sort(key=lambda r: (-r["games"], -r["winRate"], r["name"] or ""))
    return rows[:top]


def compute_biggest_upsets(players, start, end, top=5):
    """Top-N upsets this window using each player's CURRENT rating as a
    proxy for skill (not their rating at match time). Skips void/disconnected
    matches (see is_void_match) and anything without exactly one opponent
    (1v1-shaped only)."""
    rating_lookup = {}
    name_lookup = {}
    for p in players:
        pid = p.get("profileId")
        name_lookup[pid] = p.get("name")
        for ladder in ("1v1 Console", "Team Console"):
            r = p.get("ladders", {}).get(ladder, {}).get("meta", {}).get("latestRating")
            if r is not None:
                rating_lookup[(pid, ladder)] = r

    upsets = []
    seen = set()
    for p in players:
        pid = p.get("profileId")
        for ladder in ("1v1 Console", "Team Console"):
            for m in p.get("ladders", {}).get(ladder, {}).get("matches", []):
                if not in_window(m.get("date"), start, end):
                    continue
                opponents = m.get("opponents") or []
                if len(opponents) != 1:
                    continue
                key = (ladder, m.get("matchId"))
                if key in seen:
                    continue
                seen.add(key)
                if is_void_match(m):
                    continue
                opp = opponents[0]
                opp_id = opp.get("profileId")
                my_rating = rating_lookup.get((pid, ladder))
                opp_rating = rating_lookup.get((opp_id, ladder))
                if my_rating is None or opp_rating is None:
                    continue
                if m.get("won"):
                    winner_id, winner_rating = pid, my_rating
                    loser_id, loser_rating = opp_id, opp_rating
                else:
                    winner_id, winner_rating = opp_id, opp_rating
                    loser_id, loser_rating = pid, my_rating
                if winner_rating >= loser_rating:
                    continue
                upsets.append({
                    "winnerId": winner_id, "winnerName": name_lookup.get(winner_id),
                    "winnerRating": winner_rating,
                    "loserId": loser_id, "loserName": name_lookup.get(loser_id),
                    "loserRating": loser_rating,
                    "gap": loser_rating - winner_rating,
                    "map": m.get("map"), "date": m.get("date"),
                })
    upsets.sort(key=lambda r: -r["gap"])
    return upsets[:top]


def compute_civ_popularity(players, start, end, prior_start, prior_end, min_games=15, top=5):
    """topPicked/topWinRate (both carrying pickRate = games / total window
    civ-picks, same definition as build_aggregate.py's pickRate) and
    biggestMovers vs. the prior window — sorted by SIGNED pctPointChange
    (biggest gainers first, biggest losers last), not by |change|, so the
    table reads top-to-bottom as gaining-to-losing rather than jumping
    between directions."""
    def civ_counts(lo, hi):
        counts = defaultdict(lambda: {"games": 0, "wins": 0})
        for p in players:
            for ladder in ("1v1 Console", "Team Console"):
                for m in p.get("ladders", {}).get(ladder, {}).get("matches", []):
                    if not in_window(m.get("date"), lo, hi) or is_void_match(m):
                        continue
                    civ = m.get("civ")
                    if not civ:
                        continue
                    counts[civ]["games"] += 1
                    if m.get("won"):
                        counts[civ]["wins"] += 1
        return counts

    now_counts = civ_counts(start, end)
    prior_counts = civ_counts(prior_start, prior_end)
    total_now = sum(v["games"] for v in now_counts.values())
    total_prior = sum(v["games"] for v in prior_counts.values())

    def row(civ, v):
        return {
            "civ": civ, "games": v["games"],
            "pickRate": round(v["games"] / total_now, 4) if total_now else 0.0,
            "winRate": round(v["wins"] / v["games"], 4),
        }

    top_picked = sorted((row(c, v) for c, v in now_counts.items()), key=lambda r: -r["games"])[:top]
    top_win_rate = sorted(
        (row(c, v) for c, v in now_counts.items() if v["games"] >= min_games),
        key=lambda r: -r["winRate"]
    )[:top]

    movers = []
    for civ in set(now_counts) | set(prior_counts):
        now_share = now_counts[civ]["games"] / total_now if total_now else 0.0
        prior_share = prior_counts[civ]["games"] / total_prior if total_prior else 0.0
        movers.append({
            "civ": civ,
            "pctPointChange": round((now_share - prior_share) * 100, 2),
            "nowShare": round(now_share, 4),
            "priorShare": round(prior_share, 4),
        })
    movers.sort(key=lambda r: -r["pctPointChange"])
    gainers_n = -(-top // 2)  # ceil(top/2) gainers, rest losers — 3+2 for top=5
    biggest_movers = movers[:gainers_n] + movers[-(top - gainers_n):]

    return {"topPicked": top_picked, "topWinRate": top_win_rate, "biggestMovers": biggest_movers}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default="data/console")
    ap.add_argument("--window-start", required=True)
    ap.add_argument("--window-end", required=True)
    ap.add_argument("--squad-top", type=int, default=5)
    ap.add_argument("--roster-only", action="store_true",
                     help="Restrict squads to console-roster teammates only, matching snl_report.py's ratstacks.")
    ap.add_argument("--snapshot-start", help="Path to data/snapshots/console-<windowStart>.json, for mostGames Elo deltas.")
    ap.add_argument("--prior-window-start", help="Start of the prior window, for civPopularity.biggestMovers.")
    ap.add_argument("--prior-window-end", help="End of the prior window, for civPopularity.biggestMovers.")
    args = ap.parse_args()

    players = list(load_players(args.data_dir))
    roster_ids = {p.get("profileId") for p in players} if args.roster_only else None

    snapshot_start_ratings = None
    if args.snapshot_start:
        with open(args.snapshot_start, encoding="utf-8") as f:
            snapshot_start_ratings = json.load(f).get("players", {})

    out = {
        "gameBreakdown": compute_game_breakdown(players, args.window_start, args.window_end),
        "squads": compute_squads(players, args.window_start, args.window_end, top=args.squad_top, roster_ids=roster_ids),
        "pc1v1Standings": compute_pc1v1_standings(players, args.window_start, args.window_end),
        "mostGames": compute_most_games(players, args.window_start, args.window_end, snapshot_start_ratings),
        "biggestUpsets": compute_biggest_upsets(players, args.window_start, args.window_end),
        "teamRatingSnapshot": compute_team_rating_snapshot(players),
    }
    if args.prior_window_start and args.prior_window_end:
        out["civPopularity"] = compute_civ_popularity(
            players, args.window_start, args.window_end, args.prior_window_start, args.prior_window_end
        )
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
