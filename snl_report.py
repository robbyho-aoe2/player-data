#!/usr/bin/env python3
"""Saturday Night Live report: Team Console 3v3/4v4 games played Saturday
6pm EDT through Sunday 3am EDT, scoped to data/team_console_players.json
(the console roster with real Team Console history).

Window is computed from a target Saturday date. Per match, inclusion uses
the "startedAt" timestamp when a roster player's copy of the match has one
(exact), else falls back to matching the older "date" field against either
the target Saturday or the following Sunday's calendar date (an
over-inclusive approximation, since it can't see time-of-day). The report
tracks what fraction of in-window matches needed the fallback path, and
build_report() marks dataComplete false when that fraction exceeds half.

Ratstacks (duos/trios/quads) restrict squads to roster members only,
skipping untracked teammates, per the SNL report spec (deliberately
different from weekly_extras.py's compute_squads, which does not filter
teammates to any roster).

Usage:
    python3 snl_report.py --saturday 2026-09-05

Reads data/team_console_players.json + data/console/<profileId>.json and
prints the report JSON to stdout.
"""
import argparse
import datetime
import itertools
import json
from collections import defaultdict
from pathlib import Path

EDT_OFFSET = datetime.timedelta(hours=4)  # UTC-4; see DST note in report script docstring/output


def load_roster(repo_root):
    path = repo_root / "data" / "team_console_players.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except Exception:
        return []


def load_player(repo_root, profile_id):
    path = repo_root / "data" / "console" / f"{profile_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def parse_started_at(s):
    if not s:
        return None
    try:
        s2 = s.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except Exception:
        return None


def compute_window(saturday_date):
    window_start = datetime.datetime.combine(
        saturday_date, datetime.time(22, 0), tzinfo=datetime.timezone.utc
    )
    sunday_date = saturday_date + datetime.timedelta(days=1)
    window_end = datetime.datetime.combine(
        sunday_date, datetime.time(7, 0), tzinfo=datetime.timezone.utc
    )
    return window_start, window_end, sunday_date


def classify_match(match, window_start, window_end, saturday_date, sunday_date):
    """Returns (included: bool, method: 'exact'|'fallback')."""
    started_at = parse_started_at(match.get("startedAt"))
    if started_at is not None:
        included = window_start <= started_at < window_end
        return included, "exact"
    date_str = match.get("date")
    included = date_str in (saturday_date.isoformat(), sunday_date.isoformat())
    return included, "fallback"


def build_report(repo_root, saturday_date):
    window_start, window_end, sunday_date = compute_window(saturday_date)
    roster = load_roster(repo_root)

    report = {
        "date": None,  # filled in by caller
        "windowStart": window_start.isoformat().replace("+00:00", "Z"),
        "windowEnd": window_end.isoformat().replace("+00:00", "Z"),
        "dataComplete": False,
        "gamesTracked": {"total": 0, "threeVThree": 0, "fourVFour": 0},
        "mostGames": [],
        "ratstacks": {"duos": [], "trios": [], "quads": []},
    }

    if not roster:
        return report, {"exact": 0, "fallback": 0, "roster_missing": True}

    roster_set = set(roster)
    players = {}
    for pid in roster:
        p = load_player(repo_root, pid)
        if p is not None:
            players[pid] = p

    # Pass 1: determine, per unique matchId, inclusion + classification method
    # + qualifying team size, using the first roster-player copy encountered.
    match_info = {}  # matchId -> dict(size, method)
    for pid, p in players.items():
        for m in p.get("ladders", {}).get("Team Console", {}).get("matches", []):
            mid = m.get("matchId")
            if mid is None or mid in match_info:
                continue
            included, method = classify_match(m, window_start, window_end, saturday_date, sunday_date)
            if not included:
                continue
            size = 1 + len(m.get("teammates") or [])
            if size not in (3, 4):
                continue
            match_info[mid] = {"size": size, "method": method}

    exact_count = sum(1 for v in match_info.values() if v["method"] == "exact")
    fallback_count = sum(1 for v in match_info.values() if v["method"] == "fallback")
    total_in_window = exact_count + fallback_count

    games_tracked = {
        "total": total_in_window,
        "threeVThree": sum(1 for v in match_info.values() if v["size"] == 3),
        "fourVFour": sum(1 for v in match_info.values() if v["size"] == 4),
    }
    data_complete = total_in_window == 0 or fallback_count <= exact_count

    # Pass 2: per-player game counts (each roster player's own perspective;
    # a match with N in-window roster participants contributes to N players).
    player_stats = defaultdict(lambda: {"games": 0, "wins": 0, "name": None})
    for pid, p in players.items():
        name = p.get("name")
        for m in p.get("ladders", {}).get("Team Console", {}).get("matches", []):
            mid = m.get("matchId")
            if mid not in match_info:
                continue
            player_stats[pid]["name"] = name
            player_stats[pid]["games"] += 1
            if m.get("won"):
                player_stats[pid]["wins"] += 1

    most_games = []
    for pid, v in player_stats.items():
        if v["games"] == 0:
            continue
        most_games.append({
            "name": v["name"],
            "profileId": pid,
            "games": v["games"],
            "winRate": round(v["wins"] / v["games"], 4),
        })
    most_games.sort(key=lambda r: (-r["games"], -r["winRate"]))
    most_games = most_games[:15]

    # Ratstacks: squads restricted to roster members, in-window matches only,
    # deduped by matchId.
    seen = {}  # matchId -> (frozenset[(profileId, name)], won)
    for pid, p in players.items():
        name = p.get("name")
        for m in p.get("ladders", {}).get("Team Console", {}).get("matches", []):
            mid = m.get("matchId")
            if mid not in match_info or mid in seen:
                continue
            teammates = m.get("teammates") or []
            squad = {(pid, name)} | {
                (t.get("profileId"), t.get("name"))
                for t in teammates
                if t.get("profileId") in roster_set
            }
            if len(squad) >= 2:
                seen[mid] = (frozenset(squad), m.get("won"))

    stats = {n: defaultdict(lambda: {"games": 0, "wins": 0}) for n in (2, 3, 4)}
    for squad, won in seen.values():
        size = len(squad)
        for n in (2, 3, 4):
            if size < n:
                continue
            for combo in itertools.combinations(sorted(squad), n):
                stats[n][combo]["games"] += 1
                if won:
                    stats[n][combo]["wins"] += 1

    def serialize(n, top=5):
        rows = []
        for combo, v in stats[n].items():
            rows.append({
                "names": [c[1] for c in combo],
                "profileIds": [c[0] for c in combo],
                "games": v["games"],
                "winRate": round(v["wins"] / v["games"], 4),
            })
        rows.sort(key=lambda r: (-r["games"], -r["winRate"]))
        return rows[:top]

    ratstacks = {
        "duos": serialize(2),
        "trios": serialize(3),
        "quads": serialize(4),
    }

    report["dataComplete"] = data_complete
    report["gamesTracked"] = games_tracked
    report["mostGames"] = most_games
    report["ratstacks"] = ratstacks

    debug = {"exact": exact_count, "fallback": fallback_count, "roster_missing": False}
    return report, debug


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--saturday", required=True, help="Target Saturday date, YYYY-MM-DD")
    ap.add_argument("--report-date", help="Date label for the report (default: --saturday + 1 day)")
    args = ap.parse_args()

    repo_root = Path(args.repo_root)
    saturday_date = datetime.date.fromisoformat(args.saturday)
    report_date = args.report_date or (saturday_date + datetime.timedelta(days=1)).isoformat()

    report, debug = build_report(repo_root, saturday_date)
    report["date"] = report_date

    print(json.dumps(report, indent=2))
    import sys
    print(json.dumps(debug), file=sys.stderr)


if __name__ == "__main__":
    main()
