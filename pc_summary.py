#!/usr/bin/env python3
"""Compact per-player summaries for PC players (data/pc-summary/<profileId>.json.gz).

Why: the PC group is ~130,000 accounts and a full match-history file averages ~600 KB, so storing all of them in git
would add ~70 GB. What the site actually uses from a PC player is a set of win/loss counts (civ x map x patch x week x
game phase x ladder) that build_aggregate.py folds into the PC aggregates, plus the player's ratings. A summary keeps
exactly those counts, so the aggregates come out the same as they would from the full file, at roughly 3 KB per player.

The counts are stored as rows:  [ladder, civ, map, patch, week, phase, games, wins]
  week  = Monday of the match week, or null once it is older than RECENT_WEEKS_TO_KEEP (the aggregates drop those weeks
          anyway, so the all-time counts keep the games and the per-week breakdown does not need them)
  phase = "early" | "mid" | "late" by game length, or null when the duration is unknown
Everything else a summary carries (name, country, platform, per-ladder meta) matches the full-file shape, so code that
only reads ratings or country works on either.

iter_tuples() is the one reader: it yields the same tuple shape from a full file (one tuple per match) and from a
summary (one per row), which lets build_aggregate.py treat both the same way.
"""
import gzip
import json
from datetime import date, timedelta
from pathlib import Path

SUMMARY_DIRNAME = "pc-summary"
SUMMARY_FORMAT = 1
RECENT_WEEKS_TO_KEEP = 104  # keep in step with build_aggregate.py
PHASE_THRESHOLDS = [("early", 0, 1200), ("mid", 1200, 2400), ("late", 2400, 99999)]
COLS = ["ladder", "civ", "map", "patch", "week", "phase", "games", "wins"]


def summary_path(data_dir, profile_id):
    return Path(data_dir) / SUMMARY_DIRNAME / f"{profile_id}.json.gz"


def week_start(date_str):
    """Monday on or before date_str (YYYY-MM-DD), or None."""
    try:
        d = date.fromisoformat(date_str)
        return (d - timedelta(days=d.weekday())).isoformat()
    except (ValueError, TypeError):
        return None


def get_phase(dur):
    if dur is None:
        return None
    for name, lo, hi in PHASE_THRESHOLDS:
        if lo <= dur < hi:
            return name
    return None


def build_summary(player, today=None):
    """Summary dict from a full-file-shaped player dict ({name, profileId, group, ladders: {name: {meta, matches}}})."""
    cutoff = ((today or date.today()) - timedelta(weeks=RECENT_WEEKS_TO_KEEP)).isoformat()
    rows = {}
    ladders = {}
    for ladder_name, ld in player.get("ladders", {}).items():
        meta = dict(ld.get("meta") or {})
        if meta:
            ladders[ladder_name] = {"meta": meta}
        for m in ld.get("matches", []):
            civ, won = m.get("civ"), m.get("won")
            if civ is None or won is None:
                continue
            week = week_start(m.get("date"))
            if week is not None and week < cutoff:
                week = None
            key = (ladder_name, civ, m.get("map"), m.get("patch"), week, get_phase(m.get("dur")))
            cell = rows.setdefault(key, [0, 0])
            cell[0] += 1
            cell[1] += 1 if won else 0
    out = {k: player[k] for k in ("name", "profileId", "group", "country", "countryIcon", "countryName", "platform") if k in player}
    out["format"] = SUMMARY_FORMAT
    out["ladders"] = ladders
    out["cols"] = COLS
    out["rows"] = [list(k) + v for k, v in sorted(rows.items(), key=lambda kv: tuple("" if x is None else str(x) for x in kv[0]))]
    return out


def write_summary(path, summary):
    """Compact JSON, gzipped with a fixed timestamp so an unchanged summary is byte-identical (no pointless git diff)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(summary, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    with open(path, "wb") as f:
        with gzip.GzipFile(filename="", mode="wb", fileobj=f, mtime=0, compresslevel=9) as gz:
            gz.write(raw)


def load_player(path):
    """A player dict from either a full file (.json) or a summary (.json.gz)."""
    path = Path(path)
    if path.name.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def iter_tuples(player):
    """Yield (ladder, civ, map, patch, week, phase, games, wins) for every counted game of a full file or a summary.
    Matches with no civ or no recorded result are skipped, as the aggregates always did."""
    if "rows" in player:
        for ladder, civ, map_, patch, week, phase, games, wins in player["rows"]:
            yield ladder, civ, map_, patch, week, phase, games, wins
        return
    for ladder_name, ld in player.get("ladders", {}).items():
        for m in ld.get("matches", []):
            civ, won = m.get("civ"), m.get("won")
            if civ is None or won is None:
                continue
            yield ladder_name, civ, m.get("map"), m.get("patch"), week_start(m.get("date")), get_phase(m.get("dur")), 1, 1 if won else 0
