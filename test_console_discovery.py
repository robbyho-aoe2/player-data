#!/usr/bin/env python3
"""Tests for console_discovery.py and console_roster.py (the pure logic; no network).

Run directly: `python3 test_console_discovery.py`. Exits non-zero on any failure (CI gate, see tests.yml).
"""
import sys
from datetime import date

import console_discovery as cd
import console_roster as cr

FAILURES = []


def check(name, cond):
    if not cond:
        FAILURES.append(name)
        print(f"FAIL: {name}")


def ev(lb, players, finished=None, typ="matchAdded"):
    return {"type": typ, "data": {"matchId": 1, "leaderboardId": lb, "finished": finished, "players": players}}


# --- extract_console_players -------------------------------------------------------------------------------
found = cd.extract_console_players([
    ev("rm_1v1_console", [{"profileId": 10, "name": "A"}, {"profileId": 11, "name": "B"}]),
    ev("rm_team_console", [{"profileId": 12, "name": "C"}, {"profileId": -1, "name": "AI"}, {"profileId": 0, "name": "AI2"}]),
    ev("rm_1v1", [{"profileId": 20, "name": "PCGuy"}]),                       # PC ladder: ignored
    ev("unranked", [{"profileId": 21, "name": "Custom"}]),                    # custom game: ignored
    ev("rm_1v1_console", [{"profileId": 30, "name": "Done"}], finished="2026-10-14T10:00:00Z"),   # already finished
    {"type": "matchRemoved", "data": {"matchId": 1}},
])
check("console ladder players found", set(found) == {10, 11, 12})
check("AI and PC/custom players ignored", 20 not in found and 21 not in found and -1 not in found and 0 not in found and 30 not in found)
check("ladder remembered", found[12][1] == "rm_team_console")

# --- plan_changes ------------------------------------------------------------------------------------------
by_id = {
    10: {"profileId": 10, "group": "console"},
    11: {"profileId": 11, "group": "pc"},            # misfiled, no pc data -> regroup
    12: {"profileId": 12, "group": "pc"},            # misfiled, has pc data -> leave alone
    13: {"profileId": 13, "group": "pro"},           # pro stays pro
    14: {"profileId": 14, "group": "streamer"},
}
found2 = {10: ("A", "rm_1v1_console"), 11: ("B", "rm_1v1_console"), 12: ("C", "rm_team_console"),
          13: ("D", "rm_1v1_console"), 14: ("E", "rm_1v1_console"), 99: ("New", "rm_1v1_console")}
adds, regroups, skipped = cd.plan_changes(found2, by_id, lambda pid: pid == 12)
check("new player is added", [a[0] for a in adds] == [99])
check("pc without data is regrouped", [r[0] for r in regroups] == [11])
check("pc with data is skipped", [s[0] for s in skipped] == [12])
check("console/pro/streamer untouched", all(x[0] not in (10, 13, 14) for x in adds + regroups + skipped))

# --- roster merge ------------------------------------------------------------------------------------------
today = date(2026, 10, 14)
old = [
    [1, "Old1", "X", 1900, 1700, "2026-10-01", "2026-10-02"],      # on the board: updated
    [2, "Stale", "", 1500, 1500, "2025-01-01", "2025-01-01"],      # not on the board, long inactive: dropped
    [3, "Recent", "P", 1400, 0, "2026-09-20", ""],                 # not on the board but recent: kept
]
lb1 = [{"profileId": 1, "name": "Renamed", "rating": 1950, "lastMatchTime": "2026-10-13T20:00:00.000Z"},
       {"profileId": 4, "name": "Brand New", "rating": 1100, "lastMatchTime": "2026-10-14T01:00:00.000Z"}]
lbt = [{"profileId": 4, "name": "Brand New", "rating": 1050, "lastMatchTime": "2026-10-14T02:00:00.000Z"}]
rows = {r[0]: r for r in cr.merge(old, lb1, lbt, {"4": "PSN"}, today)}
check("rating and name refreshed", rows[1][1] == "Renamed" and rows[1][3] == 1950 and rows[1][4] == 1700)
check("last-match date refreshed", rows[1][5] == "2026-10-13")
check("new leaderboard player added with both ratings", rows[4][3] == 1100 and rows[4][4] == 1050 and rows[4][6] == "2026-10-14")
check("platform filled from platforms.json", rows[4][2] == "P" and rows[1][2] == "X")
check("long-inactive off-board player dropped", 2 not in rows)
check("recent off-board player kept", 3 in rows)

# --- roster additions (new console accounts that are not on a leaderboard yet) -----------------------------
roster_rows = [[10, "A", "X", 1900, 1700, "2026-10-01", "2026-10-02"]]
found3 = {10: ("A", "rm_1v1_console"), 77: ("Newbie", "rm_team_console"), 78: ("Other", "rm_1v1_console")}
new_rows = cd.roster_additions(found3, roster_rows, {"78": "PSN"}, "2026-10-15")
check("only players missing from the roster are added", [r[0] for r in new_rows] == [77, 78])
check("new rows are unrated with today's date", new_rows[0] == [77, "Newbie", "", 0, 0, "2026-10-15", "2026-10-15"])
check("platform filled from platforms.json", new_rows[1][2] == "P")
# a later leaderboard refresh keeps an unrated recent row, and fills in the rating once there is one
merged = {r[0]: r for r in cr.merge(new_rows, [{"profileId": 78, "name": "Other", "rating": 1234, "lastMatchTime": "2026-10-15T01:00:00.000Z"}], [], {}, date(2026, 10, 16))}
check("recent unrated row survives the daily rebuild", 77 in merged)
check("rating arrives from the leaderboard", merged[78][3] == 1234)

if FAILURES:
    print(f"\n{len(FAILURES)} failure(s)")
    sys.exit(1)
print("All console discovery tests passed.")
