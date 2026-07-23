#!/usr/bin/env python3
import argparse
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ELO_BRACKETS = [
    ("high",   1700, 99999),
    ("himid",  1300, 1699),
    ("lomid",   900, 1299),
    ("low",       0,  899),
]
PHASE_THRESHOLDS = [
    ("early",     0,  1200),
    ("mid",    1200,  2400),
    ("late",   2400, 99999),
]
CIV_NORM = {
    "Mayans":  "Maya",
    "Inca":    "Incas",
    "Indians": "Hindustanis",
}
CIV_SKIP = {"[civ.unknown]"}
OFFICIAL_CIVS = {
    "Armenians","Aztecs","Bengalis","Berbers","Bohemians","Britons",
    "Bulgarians","Burgundians","Burmese","Byzantines","Celts","Chinese",
    "Cumans","Dravidians","Ethiopians","Franks","Georgians","Goths",
    "Gurjaras","Hindustanis","Huns","Incas","Italians","Japanese",
    "Khmer","Koreans","Lithuanians","Magyars","Malay","Malians",
    "Maya","Mongols","Persians","Poles","Portuguese","Romans",
    "Saracens","Sicilians","Slavs","Spanish","Tatars","Teutons",
    "Turks","Vietnamese","Vikings",
    "Khitans","Shu","Wu","Jurchens","Wei","Tupi","Muisca","Mapuche",
}

ALL_GROUPS = ["console", "pro", "pc", "streamer"]

def get_bracket(rating):
    if rating is None:
        return None
    for name, lo, hi in ELO_BRACKETS:
        if lo <= rating <= hi:
            return name
    return None

def get_phase(dur):
    if dur is None:
        return None
    for name, lo, hi in PHASE_THRESHOLDS:
        if lo <= dur < hi:
            return name
    return None

def normalize_civ(civ):
    if civ is None:
        return None
    return CIV_NORM.get(civ, civ)

def week_start(date_str):
    """Return YYYY-MM-DD of the Monday on or before date_str."""
    try:
        d = date.fromisoformat(date_str)
        return (d - timedelta(days=d.weekday())).isoformat()
    except (ValueError, TypeError):
        return None

def empty_civ_map():
    return defaultdict(lambda: {"games": 0, "wins": 0})

def add_win(d, key, won):
    d[key]["games"] += 1
    if won:
        d[key]["wins"] += 1

def add_pick_rates(civ_dict):
    total = sum(v["games"] for v in civ_dict.values())
    if total == 0:
        return civ_dict
    return {
        civ: {**v, "pickRate": round(v["games"] / total, 4)}
        for civ, v in civ_dict.items()
    }

def collect_files(data_dir, group_map, cleanup=False):
    """
    Scan data subfolders and build group_files dict.
    Uses players.json group_map as the single source of truth:
      - Files in the wrong subfolder are stale (player was reassigned) — skipped
      - Duplicate profileIds are skipped (only first canonical location kept)
      - aggregate.json nested files are skipped
    Returns (group_files, stale_files).
    If cleanup=True, stale files are deleted from disk.
    """
    group_files  = {g: [] for g in ALL_GROUPS}
    seen_ids     = set()
    stale_files  = []

    for subdir in ALL_GROUPS:
        subpath = data_dir / subdir
        if not subpath.exists():
            continue
        for f in sorted(subpath.glob("*.json")):
            if f.name == "aggregate.json":
                continue
            profile_id    = f.stem
            correct_group = group_map.get(profile_id)

            if correct_group is None:
                # File exists but player is not in players.json
                print(f"  [unknown]  {f.name} — not in players.json, skipping")
                continue

            if correct_group != subdir:
                # File is in the wrong folder — stale after a group reassignment
                print(f"  [stale]    {f.name} — belongs in {correct_group}/, found in {subdir}/")
                stale_files.append(f)
                continue

            if profile_id in seen_ids:
                # Duplicate — shouldn't happen but guard anyway
                print(f"  [dup]      {profile_id} already collected, skipping {f}")
                continue

            seen_ids.add(profile_id)
            group_files[subdir].append(f)

    if stale_files:
        print(f"\n  {len(stale_files)} stale file(s) in wrong group folders.")
        if cleanup:
            for f in stale_files:
                f.unlink()
                print(f"  Deleted {f}")
        else:
            print("  Run with --cleanup to delete them automatically.")

    return group_files, stale_files

def process_group(player_files):
    civ_overall = empty_civ_map()
    by_map      = defaultdict(empty_civ_map)
    by_ladder   = defaultdict(empty_civ_map)
    by_bracket  = defaultdict(empty_civ_map)
    by_phase    = defaultdict(empty_civ_map)
    by_patch    = defaultdict(empty_civ_map)
    by_week     = defaultdict(empty_civ_map)
    total_matches = 0
    total_players = 0
    unknown_civs  = defaultdict(int)

    for path in player_files:
        with open(path) as f:
            player = json.load(f)
        total_players += 1

        console_rating = (
            player.get("ladders", {}).get("1v1 Console", {}).get("meta", {}).get("latestRating")
            or player.get("ladders", {}).get("Team Console", {}).get("meta", {}).get("latestRating")
            or player.get("ladders", {}).get("1v1 PC", {}).get("meta", {}).get("latestRating")
            or player.get("ladders", {}).get("Team PC", {}).get("meta", {}).get("latestRating")
        )
        bracket = get_bracket(console_rating)

        for ladder_name, ladder_data in player.get("ladders", {}).items():
            for match in ladder_data.get("matches", []):
                civ   = normalize_civ(match.get("civ"))
                map_  = match.get("map")
                won   = match.get("won")
                dur   = match.get("dur")
                patch = match.get("patch")
                week  = week_start(match.get("date"))

                if civ is None or won is None or civ in CIV_SKIP:
                    continue

                total_matches += 1
                phase = get_phase(dur)

                if civ not in OFFICIAL_CIVS:
                    unknown_civs[civ] += 1

                add_win(civ_overall, civ, won)

                if map_:
                    add_win(by_map[map_], civ, won)

                add_win(by_ladder[ladder_name], civ, won)

                if bracket:
                    add_win(by_bracket[bracket], civ, won)

                if phase:
                    add_win(by_phase[phase], civ, won)

                if patch is not None:
                    add_win(by_patch[str(patch)], civ, won)

                if week is not None:
                    add_win(by_week[week], civ, won)

    print(f"  players={total_players}, matches={total_matches}")
    print(f"  civs tracked: {len(civ_overall)}")
    if unknown_civs:
        print("  UNKNOWN CIVS:")
        for civ, count in sorted(unknown_civs.items(), key=lambda x: -x[1]):
            print(f"    {civ!r}: {count} games")
    print(f"  maps tracked: {len(by_map)}")
    print(f"  patches tracked: {len(by_patch)}")

    return {
        "civWinRates":  add_pick_rates(dict(civ_overall)),
        "byMap":        {m: add_pick_rates(dict(v)) for m, v in by_map.items()},
        "byLadder":     {l: add_pick_rates(dict(v)) for l, v in by_ladder.items()},
        "byEloBracket": {b: add_pick_rates(dict(v)) for b, v in by_bracket.items()},
        "byPhase":      {p: add_pick_rates(dict(v)) for p, v in by_phase.items()},
        "byPatch":      {p: add_pick_rates(dict(v)) for p, v in by_patch.items()},
        "byWeek":       {w: dict(v) for w, v in sorted(by_week.items(), reverse=True)},
    }

def build_player_summary(players_list, group_files):
    """
    Build the players array from players.json as the single source of truth.
    One entry per profileId, group from players.json (not from the file on disk).
    Players with no match file yet (newly added) are included with zero stats.
    """
    # Build profileId -> file path lookup from the already-deduplicated group_files
    file_lookup = {}
    for files in group_files.values():
        for path in files:
            file_lookup[int(path.stem)] = path

    summary = []
    for player in players_list:
        pid  = player["profileId"]
        path = file_lookup.get(pid)

        ratings     = {}
        total_games = 0

        if path is not None:
            try:
                with open(path) as f:
                    p = json.load(f)
                for ladder_name, ladder_data in p.get("ladders", {}).items():
                    meta = ladder_data.get("meta", {})
                    if meta.get("latestRating"):
                        ratings[ladder_name] = meta["latestRating"]
                    total_games += meta.get("totalGames", 0)
            except Exception as e:
                print(f"  WARNING: could not read {path}: {e}")

        summary.append({
            "name":       player.get("name", str(pid)),
            "profileId":  pid,
            "group":      player["group"],   # authoritative — from players.json
            "ratings":    ratings,
            "totalGames": total_games,
        })

    return summary

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--cleanup", action="store_true",
                        help="Delete stale player files found in wrong group folders")
    args = parser.parse_args()

    repo_root = Path(__file__).parent
    data_dir  = repo_root / "data"

    players_path = repo_root / "players.json"
    with open(players_path) as f:
        players_list = json.load(f)
    group_map = {str(p["profileId"]): p.get("group", "console") for p in players_list}

    # Collect files — players.json is the source of truth for group assignment
    group_files, stale_files = collect_files(data_dir, group_map, cleanup=args.cleanup)

    print(f"=== build_aggregate.py === {date.today()} ===")
    for g in ALL_GROUPS:
        print(f"  {g}: {len(group_files[g])} players")

    group_aggs = {}
    for g in ALL_GROUPS:
        print(f"\nProcessing {g}...")
        if group_files[g]:
            group_aggs[g] = process_group(group_files[g])
        else:
            print(f"  no players — skipping")
            group_aggs[g] = {
                "civWinRates": {}, "byMap": {}, "byLadder": {},
                "byEloBracket": {}, "byPhase": {}, "byPatch": {}, "byWeek": {}
            }

    print("\nBuilding player summary...")
    player_summary = build_player_summary(players_list, group_files)
    print(f"  players in summary: {len(player_summary)}")

    player_counts = {g: len(group_files[g]) for g in ALL_GROUPS}

    # Determine current patch — highest patch number seen across all groups
    all_patches = set()
    for g in ALL_GROUPS:
        all_patches.update(group_aggs[g]["byPatch"].keys())
    current_patch = max(all_patches, key=lambda x: int(x)) if all_patches else None

    aggregate = {
        "lastUpdated":  date.today().isoformat(),
        "currentPatch": current_patch,
        "playerCounts": player_counts,
        "players":      player_summary,
        "console":      group_aggs["console"],
        "pro":          group_aggs["pro"],
        "pc":           group_aggs["pc"],
        "streamer":     group_aggs["streamer"],
    }

    for g in ALL_GROUPS:
        total = sum(v["games"] for v in aggregate[g]["civWinRates"].values())
        if total:
            wins = sum(v["wins"] for v in aggregate[g]["civWinRates"].values())
            print(f"\n{g}: {total:,} games, {wins/total*100:.1f}% overall win rate")

    print(f"\ncurrentPatch: {current_patch}")

    if args.dry_run:
        print("\nDRY RUN - not writing aggregate.json")
        return

    out_path = data_dir / "aggregate.json"
    with open(out_path, "w") as f:
        json.dump(aggregate, f, indent=2)

    size_kb = out_path.stat().st_size / 1024
    print(f"\nWrote {out_path} ({size_kb:.1f} KB)")

if __name__ == "__main__":
    main()
