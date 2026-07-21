#!/usr/bin/env python3
"""
update_players.py — GitHub Actions pipeline script for robbyho-aoe2/player-data
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, date
from pathlib import Path

# ─── Configuration ────────────────────────────────────────────────────────────

BASE_URL      = "https://data.aoe2companion.com/api/matches"
PAGES         = 5
PAGE_DELAY    = 1.0
PLAYER_DELAY  = 3.0
TIMEOUT       = 20
MAX_DUR       = 5 * 3600

LADDER_MAP = {
    "1v1 Random Map (Console)":    "1v1 Console",
    "1v1 Random Map (Controller)": "1v1 Console",
    "Team Random Map (Console)":   "Team Console",
    "Team Random Map (Controller)":"Team Console",
    "1v1 Random Map":              "1v1 PC",
    "Team Random Map":             "Team PC",
}

CIV_NORM = {
    "Mayans": "Maya",
    "Inca":   "Incas",
}

MAP_FIELD_CANDIDATES   = ["mapName", "map"]
PATCH_FIELD_CANDIDATES = ["patch", "version", "gameVersion"]

# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_player_path(data_dir, group, profile_id):
    subfolder = data_dir / group
    subfolder.mkdir(parents=True, exist_ok=True)
    return subfolder / f"{profile_id}.json"


def api_fetch(profile_id, page):
    params = urllib.parse.urlencode({"profile_ids": profile_id, "page": page})
    url = f"{BASE_URL}?{params}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "AoE2DataPipeline/1.0 (github.com/robbyho-aoe2)"}
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
            return data.get("matches", data) if isinstance(data, dict) else data
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 30 * (attempt + 1)
                print(f"  429 rate limit — waiting {wait}s before retry {attempt + 1}/3")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Failed after 3 retries due to rate limiting")


def parse_iso(ts):
    if not ts:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def compute_duration(match):
    start = parse_iso(match.get("started"))
    end   = parse_iso(match.get("finished"))
    if not start or not end:
        return None
    dur = int((end - start).total_seconds())
    return dur if 0 < dur <= MAX_DUR else None


def first_present(d, candidates):
    for k in candidates:
        if k in d:
            return d[k]
    return None


def normalize_civ(raw):
    if raw is None:
        return None
    return CIV_NORM.get(raw, raw)


def coerce_date(ts):
    dt = parse_iso(ts)
    return dt.strftime("%Y-%m-%d") if dt else None


def find_our_player(match, profile_id):
    """
    API nests players inside teams[].players[].
    Search all teams for our profileId.
    """
    for team in match.get("teams", []):
        for player in team.get("players", []):
            if player.get("profileId") == profile_id:
                return player
    return None


def extract_match(raw, profile_id):
    lb_raw = raw.get("leaderboardName", "")
    ladder = LADDER_MAP.get(lb_raw)
    if not ladder:
        return None, None, None

    match_id = raw.get("matchId")
    if match_id is None:
        return None, None, None

    our_player = find_our_player(raw, profile_id)

    won    = our_player.get("won")    if our_player else None
    rating = our_player.get("rating") if our_player else None

    # Civ is on the player object
    civ_raw = None
    if our_player:
        civ_raw = (our_player.get("civName")
                   or our_player.get("civilizationName")
                   or our_player.get("civ"))

    civ   = normalize_civ(civ_raw)
    map_  = first_present(raw, MAP_FIELD_CANDIDATES)
    patch = first_present(raw, PATCH_FIELD_CANDIDATES)
    dur   = compute_duration(raw)
    dt    = coerce_date(raw.get("started") or raw.get("finished"))

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


# ─── Per-player update ────────────────────────────────────────────────────────

def update_player(player_def, data_dir, pages, dry_run):
    name       = player_def["name"]
    profile_id = player_def["profileId"]
    group      = player_def.get("group", "console")
    out_path   = get_player_path(data_dir, group, profile_id)

    print(f"\n[{name}] profileId={profile_id} group={group}")

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

    existing_ids = {}
    for ladder, ld in existing["ladders"].items():
        existing_ids[ladder] = {m["matchId"] for m in ld.get("matches", [])}

    latest_rating = {}
    new_matches   = {k: [] for k in existing["ladders"]}

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
            ladder, record, rating = extract_match(raw, profile_id)
            if ladder is None:
                continue

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

    today = date.today().isoformat()
    for ladder in existing["ladders"]:
        if new_matches[ladder]:
            existing["ladders"][ladder]["matches"] = (
                new_matches[ladder] + existing["ladders"][ladder]["matches"]
            )
            existing_ids[ladder].update(m["matchId"] for m in new_matches[ladder])

        meta = existing["ladders"][ladder].setdefault("meta", {})
        meta["totalGames"] = len(existing["ladders"][ladder]["matches"])
        meta["pulledDate"] = today
        if ladder in latest_rating and latest_rating[ladder] is not None:
            meta["latestRating"] = latest_rating[ladder]

    if dry_run:
        print(f"  DRY RUN — would write {out_path}")
        return False

    with open(out_path, "w") as f:
        json.dump(existing, f, indent=2)
    print(f"  wrote {out_path}")
    return True


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--players", type=str, help="Comma-separated profileIds to run (default: all)")
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

    if args.players:
        ids = {int(x.strip()) for x in args.players.split(",") if x.strip()}
        players = [p for p in players if p["profileId"] in ids]
        if not players:
            print("ERROR: none of the provided profileIds found in players.json", file=sys.stderr)
            sys.exit(1)

    print(f"=== update_players.py — {date.today()} ===")
    print(f"Players: {len(players)}, Pages: {args.pages}, Dry-run: {args.dry_run}")

    any_changed = False
    errors = []

    for i, player in enumerate(players):
        try:
            changed = update_player(player, data_dir, args.pages, args.dry_run)
            any_changed = any_changed or changed
        except Exception as e:
            msg = f"{player['name']} ({player['profileId']}): {type(e).__name__}: {e}"
            print(f"  ERROR — {msg}")
            errors.append(msg)

        if i < len(players) - 1:
            time.sleep(PLAYER_DELAY)

    print(f"\n=== Done — changed={any_changed}, errors={len(errors)} ===")
    if errors:
        print("Errors:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"changed={'true' if any_changed else 'false'}\n")


if __name__ == "__main__":
    main()
