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
with keys: totalGames, gameBreakdown, squads, pc1v1Standings, mostGames,
biggestUpsets, civPopularity, teamRatingSnapshot.
"""
import argparse
import glob
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


def compute_alltime_and_growth(players, this_date, prior_date):
    """All-time 1v1/Team totals as of `this_date` (normally today), and
    growth vs `prior_date` — both counting DISTINCT MATCHES (deduped by
    matchId), not per-player participations, so "a game" means the same
    thing here as everywhere else in the report (compute_game_breakdown,
    window1v1, etc).

    Two past bugs this avoids:
    1. A literal per-player sum (len(matches) added up across everyone)
       double-, triple-, or quadruple-counts every match with more than one
       currently-tracked participant — a 4v4 among four tracked players
       would count as 4 games, not 1. Deduping by matchId fixes that for
       BOTH allTime1v1/allTimeTeam and growth1v1/growthTeam.
    2. Naively diffing two independently-computed all-time totals re-opens
       the growth-contamination bug this function was written to fix in
       the first place: whenever the backfill pipeline finishes a batch of
       previously-untracked players, their entire (deduped) match history
       would land in "growth" in one shot. So growth1v1/growthTeam are NOT
       `allTime(this_date) - allTime(prior_date)` — they're literally
       compute_game_breakdown's own window1v1/windowTeam counts for
       (prior_date, this_date], which is already correctly scoped to
       matches actually dated inside that window. This also guarantees the
       "since last report" delta always equals the window segment's own
       number — no more of the two disagreeing.

    allTime1v1/allTimeTeam DO still need restating by `this_date` when this
    is being used to correct a PAST report, not just the current week: the
    all-time headline for a given report has to mean "distinct matches
    dated on/before THAT report's date," using today's complete roster —
    not "every match that exists in today's data regardless of date,"
    which would be today's final total misrepresented as already true back
    then. For the current week's own report, `this_date` is today, so this
    cutoff naturally includes everything and changes nothing.

    Void matches are still counted (a disconnect is still a game that was
    started)."""
    def alltime_matches(cutoff):
        seen_1v1, seen_team = set(), set()
        for p in players:
            for m in p.get("ladders", {}).get("1v1 Console", {}).get("matches", []):
                if m.get("date") and m["date"] <= cutoff:
                    seen_1v1.add(m.get("matchId"))
            for m in p.get("ladders", {}).get("Team Console", {}).get("matches", []):
                if m.get("date") and m["date"] <= cutoff:
                    seen_team.add(m.get("matchId"))
        return len(seen_1v1), len(seen_team)

    all_1v1, all_team = alltime_matches(this_date)
    gb = compute_game_breakdown(players, prior_date, this_date)
    window_1v1 = gb.get("1v1", 0)
    window_team = gb.get("total", 0) - window_1v1
    return {
        "allTime1v1": all_1v1, "allTimeTeam": all_team, "allTimeCombined": all_1v1 + all_team,
        "growth1v1": window_1v1, "growthTeam": window_team,
    }


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

    A duo is a squad that played an actual 2v2 together (not two players
    who happened to both be in a larger 3v3/4v4) — likewise trios are
    exactly 3v3s and quads exactly 4v4s. This does NOT take combinations()
    of bigger squads down into smaller ones; each match counts once, under
    its own true team size, or not at all.

    `roster_ids`, when given, restricts squad membership to teammates in
    that set — same convention as snl_report.py's ratstacks, so an
    untracked teammate doesn't get folded into a squad we can't verify. A
    match with even one untracked teammate is skipped entirely (we can't
    confirm who the full squad was), rather than counting a partial subset.
    Pass the full console profileId set here to match the SNL report's
    behavior; omit (None) for the old any-teammate behavior."""
    seen = {}  # matchId -> (tuple sorted (pid,name) of exact size, won, size)
    for p in players:
        owner_id = p.get("profileId")
        for m in p.get("ladders", {}).get("Team Console", {}).get("matches", []):
            if not in_window(m.get("date"), start, end) or is_void_match(m):
                continue
            mid = m.get("matchId")
            if mid in seen:
                continue
            teammates = m.get("teammates") or []
            true_size = 1 + len(teammates)
            if true_size not in (2, 3, 4):
                continue  # only exact 2v2/3v3/4v4 squads count
            squad = [(owner_id, p.get("name"))]
            fully_tracked = True
            for t in teammates:
                tid = t.get("profileId")
                if tid is None or (roster_ids is not None and tid not in roster_ids):
                    fully_tracked = False
                    break
                squad.append((tid, t.get("name")))
            if not fully_tracked:
                continue  # can't verify the whole squad — skip rather than guess
            seen[mid] = (tuple(sorted(squad)), m.get("won"), true_size)

    stats = {2: defaultdict(lambda: {"games": 0, "wins": 0}),
              3: defaultdict(lambda: {"games": 0, "wins": 0}),
              4: defaultdict(lambda: {"games": 0, "wins": 0})}

    for combo, won, size in seen.values():
        stats[size][combo]["games"] += 1
        if won:
            stats[size][combo]["wins"] += 1

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


def compute_pc1v1_standings(players, start, end, snapshot_start_ratings=None, top=15):
    """Current 1v1 PC rating + window games for console players with any
    1v1 PC history, plus ratingChangePC1v1 vs the window-start snapshot.

    `snapshot_start_ratings` is the same `players` dict passed to
    compute_most_games (data/snapshots/console-<windowStart>.json), read
    here for its `ratingPC1v1` field. That field is only populated once
    build_snapshot.py has captured it for at least one prior week — pass
    None (or a snapshot predating that) to leave ratingChangePC1v1 null,
    same bootstrap-week convention as elo1v1Change elsewhere in this file."""
    snapshot_start_ratings = snapshot_start_ratings or {}
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
        start_rating = (snapshot_start_ratings.get(str(p.get("profileId"))) or {}).get("ratingPC1v1")
        rows.append({
            "name": p.get("name"),
            "profileId": p.get("profileId"),
            "rating": rating,
            "totalGamesPC1v1": meta.get("totalGames", 0),
            "windowGamesPC1v1": window_games,
            "ratingChangePC1v1": (rating - start_rating) if start_rating is not None else None,
        })
    rows.sort(key=lambda r: -r["rating"])
    return rows[:top]


def compute_active_players(players, start, end):
    """Count of distinct console players with at least one non-void
    1v1 Console or Team Console match dated in the window — "players
    active this week." A void match (see is_void_match) is a disconnect
    with no recorded result, not real activity, so it doesn't count."""
    active = set()
    for p in players:
        pid = p.get("profileId")
        for ladder in ("1v1 Console", "Team Console"):
            for m in p.get("ladders", {}).get(ladder, {}).get("matches", []):
                if in_window(m.get("date"), start, end) and not is_void_match(m):
                    active.add(pid)
                    break
            if pid in active:
                break
    return len(active)


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


def compute_most_games(players, start, end, snapshot_start_ratings=None, snapshot_end_ratings=None, top=10):
    """Top-N most-active players this window (1v1 + Team Console combined),
    with finishing Elo (as of window end) and net change vs. the
    window-start snapshot, per ladder.

    `snapshot_start_ratings` is the `players` dict loaded from
    data/snapshots/console-<windowStart>.json, e.g.
    `{"13623295": {"rating1v1": 1878, ...}}` — pass None to leave every
    *Change field null (bootstrap week / no snapshot found). `ratingTeam`
    in that dict is optional and only present once compute_team_rating_snapshot
    output has been merged into snapshots for at least one prior week.

    `snapshot_end_ratings` is the same shape, loaded from
    data/snapshots/console-<windowEnd>.json. When given, "finishing Elo"
    means the rating AS OF windowEnd, from that snapshot — required when
    RESTATING a past report, since a player's live meta.latestRating
    (the fallback below) reflects whatever they're rated TODAY, not what
    they were rated at the end of that past window; the two silently
    diverge for anyone who kept playing after that window closed. Pass
    None only when generating the current week's own report same-day,
    where live rating and window-end rating are the same thing."""
    snapshot_start_ratings = snapshot_start_ratings or {}
    snapshot_end_ratings = snapshot_end_ratings or {}
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
        end_ratings = snapshot_end_ratings.get(str(pid)) or {}
        # Fall back to live per-field, not per-player — a player present
        # in the end snapshot but missing ratingTeam there (not every
        # snapshot has merged Team Console ratings yet) should still get
        # a live eloTeam rather than being reported as null.
        elo1v1 = end_ratings.get("rating1v1")
        if elo1v1 is None:
            elo1v1 = p.get("ladders", {}).get("1v1 Console", {}).get("meta", {}).get("latestRating")
        eloTeam = end_ratings.get("ratingTeam")
        if eloTeam is None:
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


def compute_biggest_upsets(players, start, end, snapshot_end_ratings=None, top=5):
    """Top-N upsets this window using each player's rating as a proxy for
    skill (not their rating at match time, which isn't tracked per-match).

    `snapshot_end_ratings` (see compute_most_games) makes that proxy the
    rating AS OF windowEnd rather than today's live rating — required
    when RESTATING a past report, since live rating drifts from the
    window-end rating for anyone who kept playing after that window
    closed, which can change not just the displayed gap but which
    matches even qualify as an upset at all. Pass None to fall back to
    live rating (fine for the current week's own same-day report).

    Skips void/disconnected matches (see is_void_match) and anything
    without exactly one opponent (1v1-shaped only)."""
    snapshot_end_ratings = snapshot_end_ratings or {}
    snapshot_key = {"1v1 Console": "rating1v1", "Team Console": "ratingTeam"}
    rating_lookup = {}
    name_lookup = {}
    for p in players:
        pid = p.get("profileId")
        name_lookup[pid] = p.get("name")
        for ladder in ("1v1 Console", "Team Console"):
            end_ratings = snapshot_end_ratings.get(str(pid))
            r = end_ratings.get(snapshot_key[ladder]) if end_ratings is not None else None
            if r is None:
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
    ap.add_argument("--snapshot-end", help="Path to data/snapshots/console-<windowEnd>.json — use when RESTATING a past report so mostGames' finishing Elo reflects that window's end, not today's live rating.")
    ap.add_argument("--prior-window-start", help="Start of the prior window, for civPopularity.biggestMovers.")
    ap.add_argument("--prior-window-end", help="End of the prior window, for civPopularity.biggestMovers.")
    args = ap.parse_args()

    players = list(load_players(args.data_dir))
    roster_ids = {p.get("profileId") for p in players} if args.roster_only else None

    snapshot_start_ratings = None
    if args.snapshot_start:
        with open(args.snapshot_start, encoding="utf-8") as f:
            snapshot_start_ratings = json.load(f).get("players", {})

    snapshot_end_ratings = None
    if args.snapshot_end:
        with open(args.snapshot_end, encoding="utf-8") as f:
            snapshot_end_ratings = json.load(f).get("players", {})

    out = {
        "totalGames": compute_alltime_and_growth(players, args.window_end, args.window_start),
        "gameBreakdown": compute_game_breakdown(players, args.window_start, args.window_end),
        "activePlayers": compute_active_players(players, args.window_start, args.window_end),
        "squads": compute_squads(players, args.window_start, args.window_end, top=args.squad_top, roster_ids=roster_ids),
        "pc1v1Standings": compute_pc1v1_standings(players, args.window_start, args.window_end, snapshot_start_ratings),
        "mostGames": compute_most_games(players, args.window_start, args.window_end, snapshot_start_ratings, snapshot_end_ratings),
        "biggestUpsets": compute_biggest_upsets(players, args.window_start, args.window_end, snapshot_end_ratings),
        "teamRatingSnapshot": compute_team_rating_snapshot(players),
    }
    if args.prior_window_start and args.prior_window_end:
        out["civPopularity"] = compute_civ_popularity(
            players, args.window_start, args.window_end, args.prior_window_start, args.prior_window_end
        )
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
