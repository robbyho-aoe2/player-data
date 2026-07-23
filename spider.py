#!/usr/bin/env python3
"""
spider.py — Discover new AoE2 players from existing match history.

Reads all match files in data/console/ and data/pc/, collects every
profileId from teams[*].players[*], probes unknown candidates against
the API, classifies them, and appends qualifying players to players.json.

Classification logic (applied after probing PROBE_PAGES pages):

  console_games >= MIN_CONSOLE_GAMES
    AND pc_games <  PC_DOMINANCE_RATIO × console_games  →  "console"

  console_games >= MIN_CONSOLE_GAMES
    AND pc_games >= PC_DOMINANCE_RATIO × console_games  →  "pc"  (ratio override)

  pc_games >= MIN_PC_GAMES  (console threshold not met)  →  "pc"

  below both thresholds                                  →  skip

Usage examples:
  python spider.py                          # standard run, up to 50 new players
  python spider.py --dry-run               # show results without writing
  python spider.py --max-new 100           # raise per-run cap
  python spider.py --probe-pages 3         # faster probe, less accurate ratio
  python spider.py --console-only          # skip PC classification
"""

import json
import time
import argparse
import requests

# ── Tunable constants ────────────────────────────────────────────────────────
MIN_CONSOLE_GAMES  = 10   # Minimum console games (in probe window) to qualify
MIN_PC_GAMES       = 10   # Minimum PC games (in probe window) to qualify
PC_DOMINANCE_RATIO = 8    # pc_games >= this × console_games → classify as PC
MAX_NEW_PLAYERS    = 50   # Cap on new players added per spider run
PROBE_PAGES        = 5    # Pages fetched per candidate (~50 matches)
RATE_LIMIT_DELAY   = 0.3  # Seconds between API calls

API_BASE = "https://data.aoe2companion.com/api/matches"

# Canonical ladder name → normalised group
LADDER_MAP = {
    "1v1 Random Map (Console)":    "console",
    "1v1 Random Map (Controller)": "console",
    "Team Random Map (Console)":   "console",
    "Team Random Map (Controller)":"console",
    "1v1 Random Map":              "pc",
    "Team Random Map":             "pc",
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def match_group(match: dict) -> str | None:
    """Return 'console', 'pc', or None for unrecognised leaderboards."""
    return LADDER_MAP.get(match.get("leaderboardName", ""))


def load_players(path: str) -> list[dict]:
    with open(path) as f:
        return json.load(f)


def save_players(players: list[dict], path: str) -> None:
    with open(path, "w") as f:
        json.dump(players, f, indent=2)


# ── Phase 1: Harvest ─────────────────────────────────────────────────────────

def harvest_candidates(players: list[dict], harvest_pages: int = 1) -> dict[int, set[str]]:
    """
    For each tracked player, fetch `harvest_pages` pages from the API and
    collect every profileId seen in teams[*].players[*].

    Local match files can't be used for this because update_players.py
    normalises the API response and strips out opponent data before writing
    to disk.  We therefore go back to the API for the harvest pass.

    Returns:
        { profileId: set_of_discovery_groups }

    Discovery group is determined by the leaderboard the match was played on.
    """
    candidates: dict[int, set[str]] = {}

    for i, player in enumerate(players, 1):
        pid = player["profileId"]
        print(f"  Harvesting {pid} ({player.get('name', '?')}) [{i}/{len(players)}]")

        for page in range(1, harvest_pages + 1):
            try:
                resp = requests.get(
                    API_BASE,
                    params={"profileId": pid, "page": page, "perPage": 10},
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"    [api error] id={pid} page={page}: {e}")
                break

            matches = data.get("matches", [])
            if not matches:
                break

            for match in matches:
                mg = match_group(match)
                if mg is None:
                    continue
                for team in match.get("teams", []):
                    for opponent in team.get("players", []):
                        oid = opponent.get("profileId")
                        if oid is None or oid == pid:
                            continue
                        if oid not in candidates:
                            candidates[oid] = set()
                        candidates[oid].add(mg)

            time.sleep(RATE_LIMIT_DELAY)

    return candidates


# ── Phase 2: Probe ───────────────────────────────────────────────────────────

def probe_player(profile_id: int, pages: int) -> tuple[int, int, str | None]:
    """
    Fetch up to `pages` pages of match history for profile_id.
    Returns (console_games, pc_games, name_or_None).

    Stops early if the API returns an empty page (reached end of history).
    """
    console_games = 0
    pc_games = 0
    name: str | None = None

    for page in range(1, pages + 1):
        try:
            resp = requests.get(
                API_BASE,
                params={"profileId": profile_id, "page": page, "perPage": 10},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"    [api error] id={profile_id} page={page}: {e}")
            break

        matches = data.get("matches", [])
        if not matches:
            break  # End of history

        for match in matches:
            mg = match_group(match)
            if mg == "console":
                console_games += 1
            elif mg == "pc":
                pc_games += 1

            # Grab name on first appearance — not all APIs return this field
            if name is None:
                for team in match.get("teams", []):
                    for p in team.get("players", []):
                        if p.get("profileId") == profile_id:
                            candidate_name = p.get("name")
                            if candidate_name:
                                name = candidate_name

        time.sleep(RATE_LIMIT_DELAY)

    return console_games, pc_games, name


# ── Phase 3: Classify ────────────────────────────────────────────────────────

def classify(
    console_games: int,
    pc_games: int,
) -> tuple[bool, str | None, str]:
    """
    Returns (qualifies, group, reason).

    Ratio override: if a player technically clears the console threshold but
    PC games dwarf console games by PC_DOMINANCE_RATIO, they're a PC player
    who happened to play a handful of console matches.
    """
    if console_games >= MIN_CONSOLE_GAMES:
        if pc_games >= PC_DOMINANCE_RATIO * console_games:
            return (
                True, "pc",
                f"ratio override: pc={pc_games} >= {PC_DOMINANCE_RATIO}× "
                f"console={console_games}",
            )
        return (
            True, "console",
            f"console={console_games}, pc={pc_games}",
        )

    if pc_games >= MIN_PC_GAMES:
        return (
            True, "pc",
            f"pc={pc_games} (console={console_games} below threshold)",
        )

    return (
        False, None,
        f"below both thresholds: console={console_games}, pc={pc_games}",
    )


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    global MIN_CONSOLE_GAMES, MIN_PC_GAMES, PC_DOMINANCE_RATIO

    parser = argparse.ArgumentParser(
        description="Spider for new AoE2 players from match history"
    )
    parser.add_argument(
        "--players", default="players.json",
        help="Path to players.json (default: players.json)",
    )
    parser.add_argument(
        "--max-new", type=int, default=MAX_NEW_PLAYERS,
        help=f"Max new players to add per run (default: {MAX_NEW_PLAYERS})",
    )
    parser.add_argument(
        "--probe-pages", type=int, default=PROBE_PAGES,
        help=f"API pages to fetch per candidate (default: {PROBE_PAGES})",
    )
    parser.add_argument(
        "--min-console", type=int, default=MIN_CONSOLE_GAMES,
        help=f"Min console games to qualify (default: {MIN_CONSOLE_GAMES})",
    )
    parser.add_argument(
        "--min-pc", type=int, default=MIN_PC_GAMES,
        help=f"Min PC games to qualify (default: {MIN_PC_GAMES})",
    )
    parser.add_argument(
        "--ratio", type=int, default=PC_DOMINANCE_RATIO,
        help=f"PC dominance ratio for override (default: {PC_DOMINANCE_RATIO})",
    )
    parser.add_argument(
        "--harvest-pages", type=int, default=1,
        help="API pages to fetch per tracked player during harvest (default: 1)",
    )
    parser.add_argument(
        "--console-only", action="store_true",
        help="Skip players classified as PC (console discovery only)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print results without writing players.json",
    )
    args = parser.parse_args()

    # Override module-level constants with CLI args so classify() picks them up
    MIN_CONSOLE_GAMES  = args.min_console
    MIN_PC_GAMES       = args.min_pc
    PC_DOMINANCE_RATIO = args.ratio

    # ── Load existing players ────────────────────────────────────────────────
    players = load_players(args.players)
    tracked: set[int] = {p["profileId"] for p in players}
    print(f"Loaded {len(players)} tracked players from {args.players}")

    # ── Harvest ──────────────────────────────────────────────────────────────
    print(f"\nHarvesting candidates via API ({args.harvest_pages} page(s) per player) …")
    all_candidates = harvest_candidates(players, harvest_pages=args.harvest_pages)
    new_candidates = {
        pid: groups
        for pid, groups in all_candidates.items()
        if pid not in tracked
    }

    print(f"  Unique IDs seen in match history: {len(all_candidates)}")
    print(f"  Already tracked:                  {len(all_candidates) - len(new_candidates)}")
    print(f"  New candidates to probe:          {len(new_candidates)}")

    if not new_candidates:
        print("\nNo new candidates. Exiting.")
        return

    # ── Probe & classify ─────────────────────────────────────────────────────
    print(f"\nProbing up to {args.max_new} new players "
          f"({args.probe_pages} pages each) …")

    added: list[dict] = []
    skipped = 0
    probed = 0
    total = len(new_candidates)

    for pid, discovery_groups in new_candidates.items():
        if len(added) >= args.max_new:
            print(f"\n  Cap of {args.max_new} reached — stopping.")
            break

        probed += 1
        disc_label = ", ".join(sorted(discovery_groups))
        print(f"\n[{probed}/{total}] profileId={pid}  discovered_via={disc_label}")

        console_games, pc_games, name = probe_player(pid, args.probe_pages)
        qualifies, group, reason = classify(console_games, pc_games)

        if not qualifies:
            print(f"  → SKIP: {reason}")
            skipped += 1
            continue

        if args.console_only and group == "pc":
            print(f"  → SKIP (--console-only, would be pc): {reason}")
            skipped += 1
            continue

        # Name may be None if the API doesn't return it in match objects;
        # update_players.py will populate it on the first weekly run.
        display_name = name or f"player_{pid}"
        print(f"  → ADD  group={group}  name={display_name!r}  ({reason})")
        added.append({
            "profileId": pid,
            "name": display_name,
            "group": group,
        })

    # ── Summary & write ──────────────────────────────────────────────────────
    print(f"\n{'─' * 50}")
    print(f"Probed:  {probed}/{total}")
    print(f"Added:   {len(added)}")
    print(f"Skipped: {skipped}")
    remaining = total - probed
    if remaining:
        print(f"Unchecked (cap reached): {remaining}")

    if args.dry_run:
        print("\n[dry-run] Would add:")
        for p in added:
            print(f"  {p}")
        print("[dry-run] players.json not written.")
    else:
        if added:
            players.extend(added)
            save_players(players, args.players)
            print(f"\nWrote {len(players)} total players to {args.players}")
        else:
            print("\nNothing to write.")


if __name__ == "__main__":
    main()
