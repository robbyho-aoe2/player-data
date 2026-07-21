#!/usr/bin/env python3
"""
migrate_to_subfolders.py — ONE-TIME migration script.

Moves data/<profileId>.json → data/<group>/<profileId>.json
based on the group field in players.json.

Run once via workflow_dispatch, then delete this file.
"""

import json
import shutil
from pathlib import Path

repo_root  = Path(__file__).parent
data_dir   = repo_root / "data"
players_path = repo_root / "players.json"

with open(players_path) as f:
    players = json.load(f)

# Build profileId → group lookup
group_map = {str(p["profileId"]): p.get("group", "console") for p in players}

moved  = 0
missing = 0

for old_path in sorted(data_dir.glob("*.json")):
    profile_id = old_path.stem
    group = group_map.get(profile_id, "console")
    new_dir  = data_dir / group
    new_dir.mkdir(parents=True, exist_ok=True)
    new_path = new_dir / old_path.name

    if new_path.exists():
        print(f"  SKIP (already exists): {new_path}")
        continue

    shutil.move(str(old_path), str(new_path))
    print(f"  moved: {old_path.name} → {group}/{old_path.name}")
    moved += 1

print(f"\nDone — moved {moved} files, {missing} missing from players.json")
