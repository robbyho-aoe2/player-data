#!/usr/bin/env python3
"""Writes data/team_console_players.json — every console-group player with
real Team Console (3v3/4v4) history, for the Saturday Night Live report to
refresh in one pass ahead of its own Sunday-morning run.

Scoped from data/console/*.json directly rather than aggregate-meta.json's
players array, since that array only exposes a group's *combined* totalGames
(1v1 Console + Team Console together) — not the per-ladder count needed to
tell "has Team Console history" apart from "1v1-only console player".

Recomputes the full qualifying set from current on-disk data every run
(idempotent) rather than diffing and appending — a player's Team Console
game count only ever grows, so a full recompute can't lose anyone, and it
self-heals if a file was ever edited or removed by hand. Run standalone to
(re)generate the roster, or as update-team-console.yml's auto-growth step
after a refresh, so newly-qualifying players are picked up before the next
Sunday run needs them.
"""
import json
from pathlib import Path


def main():
    repo_root = Path(__file__).parent
    console_dir = repo_root / "data" / "console"
    out_path = repo_root / "data" / "team_console_players.json"

    previous = set()
    if out_path.exists():
        previous = set(json.loads(out_path.read_text()))

    qualifying = []
    for fp in sorted(console_dir.glob("*.json")):
        try:
            player = json.loads(fp.read_text())
        except Exception as e:
            print(f"  WARNING: could not read {fp}: {e}")
            continue
        games = player.get("ladders", {}).get("Team Console", {}).get("meta", {}).get("totalGames")
        if games:
            qualifying.append(player["profileId"])

    qualifying.sort()
    out_path.write_text(json.dumps(qualifying, indent=2) + "\n")

    added = sorted(set(qualifying) - previous)
    removed = sorted(previous - set(qualifying))
    print(f"Team Console roster: {len(qualifying)} players ({len(added)} newly added, {len(removed)} dropped)")
    if added:
        print(f"  added: {added}")
    if removed:
        print(f"  dropped: {removed}")


if __name__ == "__main__":
    main()
