#!/usr/bin/env python3
"""
leaderboard_sync.py — Add every player currently on a live leaderboard.

Unlike spider.py (which can only discover a player if they've recently
played against someone we already track), this reads the leaderboard
directly from the API, so membership is exact — no probing/classifying
needed, and no blind spots for players who haven't crossed paths with our
existing roster.

--brackets adds everyone in the missing set (no cap unless --sample is also
given), but orders the resulting backfill-queue insertion by interleaving
across brackets (fractional-position merge) rather than concatenating one
bracket after another — so a months-long gradual backfill has a
representative skill-bracket mix at any point along the way, not just at
the end.

Usage:
  python leaderboard_sync.py --leaderboards rm_1v1_console,rm_team_console --group console
  python leaderboard_sync.py --leaderboards rm_1v1_console,rm_team_console --group console --dry-run
  python leaderboard_sync.py --leaderboards rm_1v1,rm_team --group pc \\
      --brackets '[[0,740,0.216],[741,1225,0.537],[1226,1662,0.193],[1663,999999,0.054]]'
"""

import json
import time
import argparse
import requests
from pathlib import Path

API_BASE = "https://data.aoe2companion.com/api/leaderboards"
HEADERS = {"User-Agent": "AoE2DataPipeline/1.0"}
PER_PAGE = 100
RATE_LIMIT_DELAY = 0.3


def fetch_leaderboard(leaderboard_id: str) -> list[dict]:
    """Fetch every player on a leaderboard, paginated."""
    players: list[dict] = []
    page = 1
    while True:
        for attempt in range(3):
            try:
                resp = requests.get(
                    f"{API_BASE}/{leaderboard_id}",
                    params={"page": page, "perPage": PER_PAGE},
                    headers=HEADERS,
                    timeout=20,
                )
                if resp.status_code == 429:
                    wait = [10, 30, 60][attempt]
                    print(f"    [429] rate limited — waiting {wait}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                print(f"    [error] page={page}: {e}")
                data = None
        else:
            print(f"    gave up on page {page} after 3 attempts")
            break

        if not data or not data.get("players"):
            break

        players.extend(data["players"])
        total = data.get("total", len(players))
        print(f"  {leaderboard_id} page {page}: {len(players)}/{total}")
        if len(players) >= total:
            break
        page += 1
        time.sleep(RATE_LIMIT_DELAY)

    return players


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--leaderboards", required=True,
        help="Comma-separated leaderboard IDs, e.g. rm_1v1_console,rm_team_console",
    )
    parser.add_argument("--group", required=True, help="Group to assign new players (console/pc)")
    parser.add_argument("--players", default="players.json")
    parser.add_argument(
        "--sample", type=int, default=0,
        help="If set, cap total new adds to this many (after bracket sampling, see --brackets)",
    )
    parser.add_argument(
        "--brackets", default="",
        help='JSON list of [lo, hi, share] to sample proportionally instead of adding everyone, '
             'e.g. \'[[0,1299,0.25],[1300,1699,0.35],[1700,1999,0.25],[2000,999999,0.15]]\'',
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    leaderboard_ids = [s.strip() for s in args.leaderboards.split(",") if s.strip()]

    players_path = Path(args.players)
    players = json.loads(players_path.read_text())
    tracked_ids = {p["profileId"] for p in players}

    all_lb_players: dict[int, dict] = {}
    for lb_id in leaderboard_ids:
        print(f"\nFetching {lb_id} …")
        for p in fetch_leaderboard(lb_id):
            pid = p["profileId"]
            # Keep the highest-rated appearance if a player is on multiple
            # leaderboards in this run (e.g. both console ladders).
            if pid not in all_lb_players or p.get("rating", 0) > all_lb_players[pid].get("rating", 0):
                all_lb_players[pid] = p

    print(f"\nUnique players across {len(leaderboard_ids)} leaderboard(s): {len(all_lb_players)}")

    missing = [p for pid, p in all_lb_players.items() if pid not in tracked_ids]
    print(f"Already tracked: {len(all_lb_players) - len(missing)}")
    print(f"Missing from {args.players}: {len(missing)}")

    if args.brackets:
        bracket_defs = json.loads(args.brackets)
        by_bracket: list[list[dict]] = []
        for lo, hi, *_rest in bracket_defs:
            pool = [p for p in missing if lo <= p.get("rating", 0) <= hi]
            pool.sort(key=lambda p: p.get("rating", 0), reverse=True)
            by_bracket.append(pool)

        if args.sample:
            # Optional cap: even stride through each bracket's pool down to
            # its target share, so the sample spans the bracket's range
            # rather than clustering at its ceiling.
            capped = []
            for pool, (lo, hi, share) in zip(by_bracket, bracket_defs):
                quota = round(args.sample * share)
                if quota >= len(pool):
                    capped.append(pool)
                else:
                    step = len(pool) / quota
                    capped.append([pool[int(i * step)] for i in range(quota)])
            by_bracket = capped

        for (lo, hi, *_rest), pool in zip(bracket_defs, by_bracket):
            print(f"  bracket {lo}-{hi}: {len(pool)} players")

        # Interleave brackets by fractional position (not a straight
        # concatenation) so that ANY prefix of the resulting queue —
        # not just the final total — has roughly the right bracket mix.
        # A months-long backfill processes this queue gradually, and
        # without interleaving we'd spend the first stretch only on
        # whichever bracket happened to be listed first.
        keyed: list[tuple[float, dict]] = []
        for pool in by_bracket:
            n = len(pool)
            for j, item in enumerate(pool):
                keyed.append(((j + 0.5) / n if n else 0.0, item))
        keyed.sort(key=lambda x: x[0])
        missing = [item for _, item in keyed]
    elif args.sample:
        missing.sort(key=lambda p: p.get("rating", 0), reverse=True)
        step = len(missing) / args.sample
        missing = [missing[int(i * step)] for i in range(min(args.sample, len(missing)))]

    print(f"\nWill add: {len(missing)} players (group={args.group})")

    if args.dry_run:
        print("[dry-run] Not writing players.json or backfill_queue.json.")
        return

    new_entries = [
        {"profileId": p["profileId"], "name": p.get("name") or f"player_{p['profileId']}", "group": args.group}
        for p in missing
    ]
    players.extend(new_entries)
    players_path.write_text(json.dumps(players, indent=2))
    print(f"Wrote {len(players)} total players to {args.players}")

    queue_path = Path("data") / "backfill_queue.json"
    existing_queue = json.loads(queue_path.read_text()) if queue_path.exists() else []
    # New leaderboard confirms are guaranteed-active — put them ahead of
    # whatever's already queued.
    new_ids = [e["profileId"] for e in new_entries]
    queue_path.write_text(json.dumps(new_ids + existing_queue, indent=2))
    print(f"Queued {len(new_ids)} players at the front of the backfill queue -> {queue_path}")


if __name__ == "__main__":
    main()
