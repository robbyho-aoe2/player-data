#!/usr/bin/env python3
"""
fix_pro_groups.py — One-time fix: correct group field for pro players.

Any player whose profileId has a file in data/pro/ should have
group="pro" in players.json. This script finds mismatches and fixes them.
"""

import json
from pathlib import Path

repo_root   = Path(__file__).parent
players_path = repo_root / "players.json"
pro_dir      = repo_root / "data" / "pro"

# Build set of profileIds that have a file in data/pro/
pro_ids = {int(f.stem) for f in pro_dir.glob("*.json") if f.stem != "aggregate"}
print(f"Found {len(pro_ids)} profileIds in data/pro/")

# Load players.json
with open(players_path) as f:
    players = json.load(f)

fixed = 0
for player in players:
    if player["profileId"] in pro_ids and player.get("group") != "pro":
        print(f"  Fixing {player['profileId']} ({player.get('name', '?')}): "
              f"{player.get('group')!r} → 'pro'")
        player["group"] = "pro"
        fixed += 1

print(f"\nFixed {fixed} players.")

if fixed:
    with open(players_path, "w") as f:
        json.dump(players, f, indent=2)
    print(f"Wrote updated players.json")
    print(f"\nNext step: run python3 build_aggregate.py to rebuild with correct grouping.")
else:
    print("Nothing to fix.")
