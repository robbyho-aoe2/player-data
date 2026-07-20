#!/usr/bin/env python3
"""
update_players.py — GitHub Actions pipeline script for robbyho-aoe2/player-data

Reads players.json, fetches the latest matches for each player from
aoe2companion, merges into data/<profileId>.json (dedup by matchId),
and writes updated files. Run inside the repo checkout; CI commits any diffs.

Usage:
  python3 update_players.py
  python3 update_players.py --player 13648083   # single player, for debugging
  python3 update_players.py --pages 10          # override default 5 pages
  python3 update_players.py --dry-run           # fetch but don't write files
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone, date
from pathlib import Path

# ─── Configuration ────────────────────────────────────────────────────────────

BASE_URL   = "https://data.aoe2companion.com/api/matches"
PAGES      = 5        # pages 1-5 = up to 100 most recent matches per fetch
PAGE_DELAY = 0.5      # seconds between API calls (be polite)
TIMEOUT    = 20       # request timeout seconds
MAX_DUR    = 5 * 3600 # 5 hours in seconds — cap for corrupted duration records

LADDER_MAP = {
    "1v1 Random Map (Console)":    "1v1 Console",
    "1v1 Random Map (Controller)": "1v1 Console",
    "Team Random Map (Console)":   "Team Console",
    "Team Random Map (Controller)":"Team Console",
    "1v1 Random Map":              "1v1 PC",
    "Team Random Map":             "Team PC",
}

# Civ name normalization: API name → internal tool name
CIV_NORM = {
    "Mayans": "Maya",
    "Inca":   "Incas",
}

# Which field names the API might use for civ/map/patch (checked in order)
CIV_FIELD_CANDIDATES   = ["civName", "civilizationName", "civ"]
MAP_FIELD_CANDIDATES   = ["mapType", "mapName", "map"]
PATCH_FIELD_CANDIDATES = ["version", "patch", "gameVersion"]

# ─── Helpers ──────────────────────────────────────────────────────────────────

def api_fetch(profile_id: int, page: int) -> list:
    params = urllib.parse.urlencode({"profile_ids": profile_id, "page": page})
    url = f"{BASE_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "AoE2DataPipeline/1.0 (github.com/robbyho-aoe2)"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = json.loads(resp.read().decode())
    return data.get("matches", data) if isinstance(data, dict) else dataa


def parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def compute_duration(match: dict) -> int | None:
    start = parse_iso(match.get("started"))
    end   = parse_iso(match.get("finished"))
    if not start or not end:
        return None
    dur = int((end - start).total_seconds())
    return dur if 0 < dur <= MAX_DUR else None


def first_present(d: dict, candidates: list[str]):
    for k in candidates:
        if k in d:
            return d[k]
    return None


def normalize_civ(raw: str | None) -> str | None:
    if raw is None:
        return None
    return CIV_NORM.get(raw, raw)


def coerce_date(ts: str | None) -> str | None:
    """Return YYYY-MM-DD from an ISO timestamp, or None."""
    dt = parse_iso(ts)
    return dt.strftime("%Y-%m-%d") if dt else None


def extract_match(raw: dict, profile_id: int) -> tuple[str | None, dict | None]:
    """
    Parse one raw API match into (ladder_key, normalized_match_dict).
    Returns (None, None) if the match should be skipped.
    """
    lb_raw = raw.get("leaderboardName", "")
    ladder = LADDER_MAP.get(lb_raw)
    if not ladder:
        return None, None  # unrecognised ladder (e.g. DM, Unranked) — skip

    match_id = raw.get("matchId")
    if match_id is None:
        return None, None  # can't dedup without matchId

    # Find our player object
    players = raw.get("players", [])
    our_player = next((p for p in players if p.get("profileId") == profile_id), None)

    won  = our_player.get("won") if our_player else None
    rating = our_player.get("rating") if our_player else None

    civ_raw = first_present(raw, CIV_FIELD_CANDIDATES)
    if our_player:
        # Some API versions put civ on the player object instead
        civ_raw = first_present(our_player, CIV_FIELD_CANDIDATES) or civ_raw

    civ  = normalize_civ(civ_raw)
    map_ = first_present(raw, MAP_FIELD_CANDIDATES)
    patch = first_present(raw, PATCH_FIELD_CANDIDATES)
    dur  = compute_duration(raw)
    dt   = coerce_date(raw.get("started") or raw.get("finished"))

    record = {
        "matchId": match_id,
        "civ":     civ,
        "map":     map_,
        "won":     won,
        "patch":   patch,
        "date":    dt,
        "dur":     dur,
    }
    return ladder, record, rating


# ─── Per-player update ─────────────────────────────────────────────────────────

def update_player(player_def: dict, data_dir: Path, pages: int, dry_run: bool) -> bool:
    """
    Fetch latest matches for one player, merge into their JSON file.
    Returns True if the file was modified.
    """
    name       = player_def["name"]
    profile_id = player_def["profileId"]
    group      = player_def.get("group", "console")
    out_path   = data_dir / f"{profile_id}.json"

    print(f"\n[{name}] profileId={profile_id}")

    # Load existing file (or initialise empty)
    if out_path.exists():
        with open(out_path) as f:
            existing = json.load(f)
    else:
        existing = {
            "name": name,
            "profileId": profile_id,
            "group": group,
            "ladders": {
                "1v1 Console":  {"meta": {}, "matches": []},
                "Team Console": {"meta": {}, "matches": []},
                "1v1 PC":       {"meta": {}, "matches": []},
                "Team PC":      {"meta": {}, "matches": []},
            }
        }

    # Build lookup of existing matchIds per ladder
    existing_ids: dict[str, set] = {}
    for ladder, ld in existing["ladders"].items():
        existing_ids[ladder] = {m["matchId"] for m in ld.get("matches", [])}

    # Track the most recent rating seen per ladder this run
    latest_rating: dict[str, int | None] = {}
    # Track new matches to add
    new_matches: dict[str, list] = {k: [] for k in existing["ladders"]}

    total_fetched = 0
    total_new     = 0

    for page in range(1, pages + 1):
        try:
            raw_matches = api_fetch(profile_id, page)
        except Exception as e:
            print(f"  page {page}: ERROR {type(e).__name__}: {e} — stopping pagination")
            break

        if not raw_matches:
            print(f"  page {page}: empty — done")
            break

        total_fetched += len(raw_matches)

        for raw in raw_matches:
            result = extract_match(raw, profile_id)
            if result[0] is None:
                continue
            ladder, record, rating = result

            # Track latest rating (page 1 matches are most recent)
            if rating is not None and ladder not in latest_rating:
                latest_rating[ladder] = rating

            if record["matchId"] not in existing_ids.get(ladder, set()):
                new_matches[ladder].append(record)
                total_new += 1

        print(f"  page {page}: fetched {len(raw_matches)} matches")
        if page < pages:
            time.sleep(PAGE_DELAY)

    print(f"  total fetched={total_fetched}, new={total_new}")

    if total_new == 0 and not latest_rating:
        print(f"  no changes — skipping write")
        return False

    # Merge new matches into existing (prepend — newest first)
    today = date.today().isoformat()
    for ladder in existing["ladders"]:
        if new_matches[ladder]:
            existing["ladders"][ladder]["matches"] = (
                new_matches[ladder] + existing["ladders"][ladder]["matches"]
            )
            existing_ids[ladder].update(m["matchId"] for m in new_matches[ladder])

        # Update meta
        meta = existing["ladders"][ladder].setdefault("meta", {})
        meta["totalGames"] = len(existing["ladders"][ladder]["matches"])
        meta["pulledDate"] = today
        if ladder in latest_rating and latest_rating[ladder] is not None:
            meta["latestRating"] = latest_rating[ladder]

    if dry_run:
        print(f"  DRY RUN — would write {out_path.name}")
        return False

    with open(out_path, "w") as f:
        json.dump(existing, f, indent=2)
    print(f"  wrote {out_path.name}")
    return True


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--player", type=int, help="Run for a single profileId only")
    parser.add_argument("--pages", type=int, default=PAGES, help=f"Pages to fetch per player (default {PAGES})")
    parser.add_argument("--dry-run", action="store_true", help="Fetch but don't write files")
    args = parser.parse_args()

    repo_root = Path(__file__).parent
    data_dir  = repo_root / "data"
    data_dir.mkdir(exist_ok=True)

    players_path = repo_root / "players.json"
    if not players_path.exists():
        print("ERROR: players.json not found", file=sys.stderr)
        sys.exit(1)

    with open(players_path) as f:
        players = json.load(f)

    if args.player:
        players = [p for p in players if p["profileId"] == args.player]
        if not players:
            print(f"ERROR: profileId {args.player} not in players.json", file=sys.stderr)
            sys.exit(1)

    print(f"=== update_players.py — {date.today()} ===")
    print(f"Players: {len(players)}, Pages: {args.pages}, Dry-run: {args.dry_run}")

    any_changed = False
    errors = []

    for player in players:
        try:
            changed = update_player(player, data_dir, args.pages, args.dry_run)
            any_changed = any_changed or changed
        except Exception as e:
            msg = f"{player['name']} ({player['profileId']}): {type(e).__name__}: {e}"
            print(f"  ERROR — {msg}")
            errors.append(msg)

    print(f"\n=== Done — changed={any_changed}, errors={len(errors)} ===")
    if errors:
        print("Errors:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    # Signal to CI whether there's anything to commit
    # GitHub Actions can check ${{ steps.update.outputs.changed }}
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"changed={'true' if any_changed else 'false'}\n")


if __name__ == "__main__":
    main()
