#!/usr/bin/env python3
"""Writes data/pipeline_health.json — a small per-run snapshot that lets
external automation (e.g. the weekly-report routine, which otherwise only
has its own blunt "implausibly low total" heuristic to go on) check
freshWithin7DaysPct before trusting a week's numbers, instead of assuming
the refresh pipeline is keeping up.

Run by update-players.yml's "Write pipeline health" step, after Build
aggregate and before the final commit — DATA_CHANGED must be computed from
`git status` before this script writes the file, since the file's own
timestamp would otherwise always show a diff.
"""
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

FRESHNESS_WINDOW_DAYS = 7


def main():
    changed = os.environ.get("DATA_CHANGED", "false") == "true"
    batch_size = int(os.environ["REFRESH_BATCH_SIZE"])

    repo_root = Path(__file__).parent
    data_dir = repo_root / "data"

    players = json.loads((repo_root / "players.json").read_text())
    scoped_paths = [
        data_dir / p.get("group", "console") / f"{p['profileId']}.json"
        for p in players
    ]
    scoped_paths = [p for p in scoped_paths if p.exists()]
    scoped_pool_size = len(scoped_paths)

    cursor_path = data_dir / "refresh_cursor.json"
    cursor_index = json.loads(cursor_path.read_text())["index"] if cursor_path.exists() else 0

    cutoff = (date.today() - timedelta(days=FRESHNESS_WINDOW_DAYS)).isoformat()
    fresh_count = 0
    for path in scoped_paths:
        try:
            player = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        pulled_dates = [
            ladder.get("meta", {}).get("pulledDate")
            for ladder in player.get("ladders", {}).values()
        ]
        pulled_dates = [d for d in pulled_dates if d]
        if pulled_dates and max(pulled_dates) >= cutoff:
            fresh_count += 1

    fresh_pct = round(100 * fresh_count / scoped_pool_size) if scoped_pool_size else 0

    health = {
        "lastRunAt": datetime.now(timezone.utc).isoformat(),
        "lastRunConclusion": "success" if changed else "no_changes",
        "cursorIndex": cursor_index,
        "scopedPoolSize": scoped_pool_size,
        "batchSize": batch_size,
        "freshWithin7Days": fresh_count,
        "freshWithin7DaysPct": fresh_pct,
    }

    health_path = data_dir / "pipeline_health.json"
    health_path.write_text(json.dumps(health, indent=2))
    print(f"Wrote {health_path}: {health}")


if __name__ == "__main__":
    main()
