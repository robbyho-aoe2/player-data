#!/usr/bin/env python3
"""
find_empty_players.py — Find players with no match data.

Checks every player in players.json against their data file.
Outputs a comma-separated profileId list ready to paste into
workflow_dispatch player_ids for a targeted update run.
"""

import json
from pathlib import Path

repo_root = Path(__file__).parent
data_dir  = repo_root / "data"

with open(repo_root / "players.json") as f:
    players = json.load(f)

empty = []

for player in players:
    pid   = player["profileId"]
    group = player.get("group", "console")
    name  = player.get("name", str(pid))
    path  = data_dir / group / f"{pid}.json"

    if not path.exists():
        print(f"  NO FILE   {pid:12}  {name}  ({group})")
        empty.append(pid)
        continue

    try:
        with open(path) as f:
            data = json.load(f)
    except Exception as e:
        print(f"  BAD FILE  {pid:12}  {name}  ({group}) — {e}")
        empty.append(pid)
        continue

    total_matches = sum(
        len(ladder.get("matches", []))
        for ladder in data.get("ladders", {}).values()
    )

    if total_matches == 0:
        print(f"  NO GAMES  {pid:12}  {name}  ({group})")
        empty.append(pid)

print(f"\nTotal with no data: {len(empty)} / {len(players)}")

if empty:
    print(f"\nPaste into workflow_dispatch player_ids:")
    print(",".join(str(i) for i in empty))
