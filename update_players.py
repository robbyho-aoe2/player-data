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
PROFILE_URL   = "https://data.aoe2companion.com/api/profiles"
PAGES         = 1
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

# leaderboardId strings as returned by /api/profiles/{id} -> our internal
# ladder names. Separate from LADDER_MAP (which parses match records) since
# the profile endpoint uses its own short ids.
PROFILE_LADDER_MAP = {
    "rm_1v1_console":  "1v1 Console",
    "rm_team_console": "Team Console",
    "rm_1v1":          "1v1 PC",
    "rm_team":         "Team PC",
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


def fetch_peak_ratings(profile_id):
    """Highest-ever rating per ladder, straight from aoe2companion's own
    rating-change history (/api/profiles/{id}). meta.latestRating alone
    understates a player who's since gone quiet — the live leaderboard
    endpoint drops anyone not currently ranked this season, so a player's
    true peak can only be recovered from their full history, not from
    scanning the active leaderboard."""
    url = f"{PROFILE_URL}/{profile_id}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "AoE2DataPipeline/1.0 (github.com/robbyho-aoe2)"}
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
            break
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 30 * (attempt + 1)
                print(f"  429 rate limit (profile) — waiting {wait}s before retry {attempt + 1}/3")
                time.sleep(wait)
            elif e.code == 404:
                return {}
            else:
                raise
    else:
        raise RuntimeError("Failed after 3 retries due to rate limiting (profile)")

    peaks = {}
    for entry in data.get("ratings", []):
        ladder = PROFILE_LADDER_MAP.get(entry.get("leaderboardId"))
        if not ladder:
            continue
        hist = [x["rating"] for x in (entry.get("ratings") or []) if x.get("rating") is not None]
        if hist:
            peaks[ladder] = max(hist)
    return peaks


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
    for team in match.get("teams", []):
        for player in team.get("players", []):
            if player.get("profileId") == profile_id:
                return player
    return None


def get_opponents(match, profile_id):
    """Players on any team OTHER than ours — one entry for 1v1s, several
    for team games. Used for head-to-head stats (e.g. Player Spotlight's
    top-opponents table)."""
    teams = match.get("teams", [])
    our_team_idx = None
    for i, team in enumerate(teams):
        if any(p.get("profileId") == profile_id for p in team.get("players", [])):
            our_team_idx = i
            break
    if our_team_idx is None:
        return []
    opponents = []
    for i, team in enumerate(teams):
        if i == our_team_idx:
            continue
        for player in team.get("players", []):
            pid = player.get("profileId")
            if pid is not None:
                opponents.append({"profileId": pid, "name": player.get("name")})
    return opponents


def get_teammates(match, profile_id):
    """Players on the SAME team as us, excluding ourselves. Empty for 1v1s.
    Used for squad detection (e.g. Insights' top duos/trios/quads)."""
    teams = match.get("teams", [])
    for team in teams:
        players = team.get("players", [])
        if any(p.get("profileId") == profile_id for p in players):
            return [
                {"profileId": p.get("profileId"), "name": p.get("name")}
                for p in players
                if p.get("profileId") is not None and p.get("profileId") != profile_id
            ]
    return []


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
    opponents = get_opponents(raw, profile_id)
    teammates = get_teammates(raw, profile_id)

    record = {
        "matchId":   match_id,
        "civ":       civ,
        "map":       map_,
        "won":       won,
        "patch":     patch,
        "date":      dt,
        "dur":       dur,
        "opponents": opponents,
        "teammates": teammates,
    }
    return ladder, record, rating


# ─── Per-player update ────────────────────────────────────────────────────────

def update_player(player_def, data_dir, pages, dry_run, repair):
    name       = player_def["name"]
    profile_id = player_def["profileId"]
    group      = player_def.get("group", "console")
    out_path   = get_player_path(data_dir, group, profile_id)

    print(f"\n[{name}] profileId={profile_id} group={group}")

    if out_path.exists():
        with open(out_path) as f:
            existing = json.load(f)
        if repair:
            for ladder in existing["ladders"]:
                existing["ladders"][ladder]["matches"] = []
                existing["ladders"][ladder]["meta"] = {}
            print(f"  REPAIR — cleared existing match data")
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
    found_name    = None  # Real name if found in API response

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
        page_new = 0

        for raw in raw_matches:
            ladder, record, rating = extract_match(raw, profile_id)
            if ladder is None:
                continue

            if rating is not None and ladder not in latest_rating:
                latest_rating[ladder] = rating

            if record["matchId"] not in existing_ids.get(ladder, set()):
                new_matches[ladder].append(record)
                total_new += 1
                page_new += 1

            # Capture real name from match data if still unknown
            if found_name is None:
                our_player = find_our_player(raw, profile_id)
                if our_player:
                    candidate = our_player.get("name")
                    if candidate and not candidate.startswith("player_"):
                        found_name = candidate

        print(f"  page {page}: fetched {len(raw_matches)} matches, {page_new} new")

        # Matches come back newest-first, so once a page contributes zero
        # new matches, every older page would too — no point paying for
        # them. `pages` is just a safety cap for how far a genuinely
        # inactive-since-last-check player might need; this makes the
        # common case (routine refresh, already caught up) stop after one
        # page instead of always walking the full cap.
        if page_new == 0 and existing_ids and any(existing_ids.values()):
            print(f"  page {page}: no new matches — caught up, stopping")
            break

        if page < pages:
            time.sleep(PAGE_DELAY)

    print(f"  total fetched={total_fetched}, new={total_new}")

    try:
        peak_ratings = fetch_peak_ratings(profile_id)
    except Exception as e:
        print(f"  peak-rating fetch failed: {type(e).__name__}: {e}")
        peak_ratings = {}

    peak_changed = any(
        (peak_ratings.get(ladder) or 0) > (existing["ladders"][ladder].get("meta", {}).get("peakRating") or 0)
        for ladder in existing["ladders"]
    )

    if total_new == 0 and not latest_rating and not peak_changed:
        print(f"  no changes — skipping write")
        return False, found_name, None

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
        new_peak = peak_ratings.get(ladder)
        if new_peak is not None:
            meta["peakRating"] = max(meta.get("peakRating") or 0, new_peak)

    # Self-healing group check: crossplay means a "pc"-tracked account can
    # turn out to have real console-ladder history (they were only found on
    # a PC leaderboard scan first, or dabble in crossplay) — any console
    # games at all means they're almost certainly a console player. Catching
    # it here, at the moment their data is actually fetched, is far more
    # scalable than a one-time bulk audit against the live API.
    reclassified_group = None
    if group == "pc":
        console_games = (
            len(existing["ladders"].get("1v1 Console", {}).get("matches", []))
            + len(existing["ladders"].get("Team Console", {}).get("matches", []))
        )
        if console_games > 0:
            print(f"  RECLASSIFY — {console_games} console-ladder games found, pc -> console")
            reclassified_group = "console"
            existing["group"] = "console"
            new_out_path = get_player_path(data_dir, "console", profile_id)
            if dry_run:
                print(f"  DRY RUN — would move to {new_out_path}")
            else:
                with open(new_out_path, "w") as f:
                    json.dump(existing, f, indent=2)
                out_path.unlink(missing_ok=True)
                print(f"  moved {out_path} -> {new_out_path}")
            return True, found_name, reclassified_group

    if dry_run:
        print(f"  DRY RUN — would write {out_path}")
        return False, found_name, None

    with open(out_path, "w") as f:
        json.dump(existing, f, indent=2)
    print(f"  wrote {out_path}")
    if found_name and found_name != name:
        print(f"  found real name: {found_name!r}")
    return True, found_name, None


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--players", type=str, help="Comma-separated profileIds to run (default: all)")
    parser.add_argument("--pages", type=int, default=PAGES, help=f"Pages to fetch per player (default {PAGES})")
    parser.add_argument("--dry-run", action="store_true", help="Fetch but don't write files")
    parser.add_argument("--repair", action="store_true", help="Clear existing match data before re-fetching (fixes null civ/won)")
    parser.add_argument("--group", type=str, default="console", help="Group for newly added players (default: console)")
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

    if not args.players:
        # Default scope is "refresh players we've already backfilled at
        # least once" — NOT everyone in players.json. leaderboard_sync.py
        # can queue tens of thousands of newly-discovered players at a
        # time, and most of those have no data file yet; a full-roster
        # refresh pass would then try to touch all of them with no route
        # to actually fetch their first history (that's backfill.yml's
        # job), ballooning run time for no benefit and starving the
        # shared concurrency lock. Only a data file on disk means a
        # player is in this step's scope.
        before = len(players)
        players = [p for p in players if get_player_path(data_dir, p.get("group", "console"), p["profileId"]).exists()]
        skipped = before - len(players)
        if skipped:
            print(f"Scoped to {len(players)} already-backfilled players (skipped {skipped} not yet backfilled)")

    if args.players:
        ids = {int(x.strip()) for x in args.players.split(",") if x.strip()}
        known = {p["profileId"]: p for p in players}
        players = []
        new_players = []

        for uid in sorted(ids):
            if uid in known:
                players.append(known[uid])
            else:
                # Auto-add unknown players rather than failing
                entry = {"profileId": uid, "name": f"player_{uid}", "group": args.group}
                players.append(entry)
                new_players.append(entry)
                print(f"  [new] Adding {uid} as group={args.group}")

        if new_players and not args.dry_run:
            with open(players_path) as f:
                all_players = json.load(f)
            all_players.extend(new_players)
            with open(players_path, "w") as f:
                json.dump(all_players, f, indent=2)
            print(f"  Added {len(new_players)} new player(s) to players.json")

    print(f"=== update_players.py — {date.today()} ===")
    print(f"Players: {len(players)}, Pages: {args.pages}, Dry-run: {args.dry_run}, Repair: {args.repair}")

    any_changed = False
    needs_name_update = False
    needs_group_update = False
    errors = []

    for i, player in enumerate(players):
        try:
            changed, real_name, new_group = update_player(player, data_dir, args.pages, args.dry_run, args.repair)
            any_changed = any_changed or changed
            # Update players.json if we found a real name for a placeholder entry
            if real_name and player["name"].startswith("player_") and not args.dry_run:
                player["name"] = real_name
                needs_name_update = True
            if new_group and not args.dry_run:
                player["group"] = new_group
                needs_group_update = True
        except Exception as e:
            msg = f"{player['name']} ({player['profileId']}): {type(e).__name__}: {e}"
            print(f"  ERROR — {msg}")
            errors.append(msg)

        if i < len(players) - 1:
            time.sleep(PLAYER_DELAY)

    if (needs_name_update or needs_group_update) and not args.dry_run:
        with open(players_path) as f:
            all_players = json.load(f)
        name_map = {p["profileId"]: p["name"] for p in players}
        group_map = {p["profileId"]: p["group"] for p in players}
        for p in all_players:
            if p["profileId"] in name_map:
                p["name"] = name_map[p["profileId"]]
            if p["profileId"] in group_map:
                p["group"] = group_map[p["profileId"]]
        with open(players_path, "w") as f:
            json.dump(all_players, f, indent=2)
        if needs_name_update:
            print("  Updated player names in players.json")
        if needs_group_update:
            print("  Updated player groups in players.json")

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
