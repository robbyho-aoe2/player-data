#!/usr/bin/env python3
"""Tests for pc_summary.py and its use in build_aggregate.py / update_players.py (no network).

Run directly: `python3 test_pc_summary.py`. Exits non-zero on any failure (CI gate, see tests.yml).
The one that matters: the PC aggregates built from a summary must match the ones built from the full file.
"""
import gzip
import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import build_aggregate as ba
import pc_summary as ps
import update_players as up

FAILURES = []


def check(name, cond):
    if not cond:
        FAILURES.append(name)
        print(f"FAIL: {name}")


def d(days_ago):
    return (date.today() - timedelta(days=days_ago)).isoformat()


def match(i, civ, map_, won, days_ago, dur=1500, patch=1815):
    return {"matchId": i, "civ": civ, "map": map_, "won": won, "patch": patch, "date": d(days_ago), "dur": dur,
            "opponents": [], "teammates": []}


PLAYER = {
    "name": "Pcguy", "profileId": 4242, "group": "pc", "country": "us", "countryIcon": "x", "countryName": "United States",
    "platform": "Steam",
    "ladders": {
        "1v1 Console": {"meta": {}, "matches": []},
        "Team Console": {"meta": {}, "matches": []},
        "1v1 PC": {"meta": {"latestRating": 1650, "peakRating": 1700, "totalGames": 7}, "matches": [
            match(1, "Franks", "Arabia", True, 3), match(2, "Franks", "Arabia", False, 4, dur=900),
            match(3, "Mayans", "Arena", True, 10, dur=3000), match(4, "Franks", "Arabia", True, 900, patch=1500),
            match(5, None, "Arabia", True, 5), match(6, "Huns", "Arabia", None, 6), match(7, "Huns", None, True, 7, dur=None)]},
        "Team PC": {"meta": {"latestRating": 1400, "totalGames": 2}, "matches": [
            match(8, "Franks", "Arabia", False, 3), match(9, "Franks", "Black Forest", True, 2000)]},
    },
}

# --- summary content ------------------------------------------------------------------------------------------
summ = ps.build_summary(PLAYER)
check("summary keeps identity", summ["name"] == "Pcguy" and summ["country"] == "us" and summ["profileId"] == 4242)
check("summary keeps ratings meta", summ["ladders"]["1v1 PC"]["meta"]["latestRating"] == 1650 and "1v1 Console" not in summ["ladders"])
games = sum(r[6] for r in summ["rows"])
check("summary counts only games with a civ and a result", games == 7)  # 9 matches minus the null civ and the null result
check("old weeks are dropped to null", any(r[4] is None and r[1] == "Franks" and r[6] == 1 and r[3] == 1500 for r in summ["rows"]))

# --- file round trip + determinism -----------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    sp = ps.summary_path(tmp, 4242)
    ps.write_summary(sp, summ)
    first = sp.read_bytes()
    ps.write_summary(sp, ps.build_summary(PLAYER))
    check("summary file is deterministic", sp.read_bytes() == first)
    check("summary is gzip", first[:2] == b"\x1f\x8b")
    check("summary round trips", ps.load_player(sp) == json.loads(json.dumps(summ)))
    check("summary is much smaller than the full file", len(first) < len(json.dumps(PLAYER)))

    # --- the aggregate from a full file equals the aggregate from its summary ---------------------------------
    full_path = tmp / "pc" / "4242.json"
    full_path.parent.mkdir()
    full_path.write_text(json.dumps(PLAYER))
    a_full = ba.process_group([full_path], group="pc")
    a_summ = ba.process_group([sp], group="pc")
    check("process_group: summary equals full file", json.dumps(a_full, sort_keys=True) == json.dumps(a_summ, sort_keys=True))
    c_full = ba.process_pc_civs([full_path])
    c_summ = ba.process_pc_civs([sp])
    check("process_pc_civs: summary equals full file", json.dumps(c_full, sort_keys=True) == json.dumps(c_summ, sort_keys=True))
    check("PC bracket comes from the PC rating (1650 -> himid)", "himid" in c_summ["brackets"] and "high" not in c_summ["brackets"])

    # --- collect_pc_summaries ------------------------------------------------------------------------------------
    gm = {"4242": "pc", "5": "console", "6": "pc"}
    ps.write_summary(ps.summary_path(tmp, 5), ps.build_summary({**PLAYER, "profileId": 5}))
    ps.write_summary(ps.summary_path(tmp, 6), ps.build_summary({**PLAYER, "profileId": 6}))
    got = sorted(p.name for p in ba.collect_pc_summaries(tmp, gm, set()))
    check("summaries of players moved to console are ignored", got == ["4242.json.gz", "6.json.gz"])
    got = sorted(p.name for p in ba.collect_pc_summaries(tmp, gm, {"6"}))
    check("players with a full file are not double counted", got == ["4242.json.gz"])

    # --- update_player writes a summary (not a full file) for a new PC player ---------------------------------
    def fake_matches(profile_id, page):
        if page > 1:
            return []
        return [{"matchId": 77, "leaderboardName": "1v1 Random Map", "started": f"{d(2)}T10:00:00.000Z", "finished": f"{d(2)}T10:20:00.000Z",
                 "mapName": "Arabia", "map": "rm_arabia", "patch": 1815,
                 "teams": [{"players": [{"profileId": profile_id, "name": "Pcguy", "civName": "Franks", "won": True}]},
                           {"players": [{"profileId": 9, "name": "Opp", "civName": "Huns", "won": False}]}]}]
    orig_fetch, orig_prof, orig_delay = up.api_fetch, up.fetch_profile_ratings, up.PAGE_DELAY
    up.api_fetch = fake_matches
    up.fetch_profile_ratings = lambda pid: ({"1v1 PC": {"current": 1600, "peak": 1650}}, {"country": "us", "countryIcon": "x", "countryName": "United States", "platform": "Steam"})
    up.PAGE_DELAY = 0
    try:
        data = tmp / "data"
        changed, _, regroup = up.update_player({"name": "Pcguy", "profileId": 4243, "group": "pc"}, data, 1, False, False)
        check("new PC player: changed, no regroup", changed and regroup is None)
        check("new PC player: summary written", ps.summary_path(data, 4243).exists())
        check("new PC player: no full file", not (data / "pc" / "4243.json").exists())
        s2 = ps.load_player(ps.summary_path(data, 4243))
        check("new PC player: summary has the game and rating", sum(r[6] for r in s2["rows"]) == 1 and s2["ladders"]["1v1 PC"]["meta"]["latestRating"] == 1600)
        # a console player still gets a full file
        changed, _, _ = up.update_player({"name": "Conny", "profileId": 4244, "group": "console"}, data, 1, False, False)
        check("console player still gets a full file", (data / "console" / "4244.json").exists() and not ps.summary_path(data, 4244).exists())
    finally:
        up.api_fetch, up.fetch_profile_ratings, up.PAGE_DELAY = orig_fetch, orig_prof, orig_delay

if FAILURES:
    print(f"\n{len(FAILURES)} FAILED")
    sys.exit(1)
print("all pc_summary tests passed")
