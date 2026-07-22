#!/usr/bin/env python3
import argparse
import json
from collections import defaultdict
from datetime import date
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
KNOWN_MAPS = {
    "Arabia","Arena","Nomad","Black Forest","Islands","Hideout",
    "Mega Random","MegaRandom","Four Lakes","Gold Rush","Migration",
    "Baltic","Continental","Fortress","Ghost Lake","Hill Fort",
    "Lombardia","Mediterranean","Mongolia","Serengeti","Steppe",
    "Valley","Wolf Hill","Alpine Lakes","Amazon Tunnel","Archipelago",
    "Budapest","Cenotes","City of Lakes","Coastal","Coastal Forest",
    "Cross","Eruption","Frigid Lake","Golden Pit","Haunted Wasteland",
    "Kawasan","Kilimanjaro","Land Nomad","Mountain Pass","Nile Delta",
    "Oasis","Pacific Islands","Ravines","Rivers","Sacred Springs",
    "Scandinavia","Shoals","Team Islands","Yucatan",
}
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

def process_group(player_files):
    civ_overall = empty_civ_map()
    by_map      = defaultdict(empty_civ_map)
    by_ladder   = defaultdict(empty_civ_map)
    by_bracket  = defaultdict(empty_civ_map)
    by_phase    = defaultdict(empty_civ_map)
    total_matches = 0
    total_players = 0
    unknown_civs  = defaultdict(int)
    for path in player_files:
        with open(path) as f:
            player = json.load(f)
        total_players += 1
        for ladder_name, ladder_data in player.get("ladders", {}).items():
            rating  = ladder_data.get("meta", {}).get("latestRating")
            bracket = get_bracket(rating)
            for match in ladder_data.get("matches", []):
                civ  = normalize_civ(match.get("civ"))
                map_ = match.get("map")
                won  = match.get("won")
                dur  = match.get("dur")
                if civ is None or won is None or civ in CIV_SKIP:
                    continue
                total_matches += 1
                phase = get_phase(dur)
                if civ not in OFFICIAL_CIVS:
                    unknown_civs[civ] += 1
                add_win(civ_overall, civ, won)
                if map_:
                    display_map = map_ if map_ in KNOWN_MAPS else "Other"
                    add_win(by_map[display_map], civ, won)
                add_win(by_ladder[ladder_name], civ, won)
                if bracket:
                    add_win(by_bracket[bracket], civ, won)
                if phase:
                    add_win(by_phase[phase], civ, won)
    print(f"  players={total_players}, matches={total_matches}")
    print(f"  civs tracked: {len(civ_overall)}")
    if unknown_civs:
        print("  UNKNOWN CIVS:")
        for civ, count in sorted(unknown_civs.items(), key=lambda x: -x[1]):
            print(f"    {civ!r}: {count} games")
    print(f"  maps tracked: {len(by_map)}")
    return {
        "civWinRates":  add_pick_rates(dict(civ_overall)),
        "byMap":        {m: add_pick_rates(dict(v)) for m, v in by_map.items()},
        "byLadder":     {l: add_pick_rates(dict(v)) for l, v in by_ladder.items()},
        "byEloBracket": {b: add_pick_rates(dict(v)) for b, v in by_bracket.items()},
        "byPhase":      {p: add_pick_rates(dict(v)) for p, v in by_phase.items()},
    }

def build_player_summary(all_files):
    summary = []
    for path in all_files:
        with open(path) as f:
            p = json.load(f)
        if "profileId" not in p:
            print(f"  WARNING: missing profileId in {path.name} — skipping")
            continue
        ratings = {}
        total_games = 0
        for ladder_name, ladder_data in p.get("ladders", {}).items():
            meta = ladder_data.get("meta", {})
            if meta.get("latestRating"):
                ratings[ladder_name] = meta["latestRating"]
            total_games += meta.get("totalGames", 0)
        summary.append({
            "name":       p.get("name", str(p["profileId"])),
            "profileId":  p["profileId"],
            "group":      p.get("group", "console"),
            "ratings":    ratings,
            "totalGames": total_games,
        })
    return summary

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    repo_root = Path(__file__).parent
    data_dir  = repo_root / "data"
    console_files = sorted((data_dir / "console").glob("*.json")) if (data_dir / "console").exists() else []
    pc_files      = sorted((data_dir / "pc").glob("*.json"))      if (data_dir / "pc").exists()      else []
    print(f"=== build_aggregate.py === {date.today()} ===")
    print(f"Console players: {len(console_files)}, PC players: {len(pc_files)}")
    print("\nProcessing console...")
    console_agg = process_group(console_files)
    print("\nProcessing PC...")
    pc_agg = process_group(pc_files)
    print("\nBuilding player summary...")
    player_summary = build_player_summary(console_files + pc_files)
    print(f"  players in summary: {len(player_summary)}")
    aggregate = {
        "lastUpdated":  date.today().isoformat(),
        "playerCounts": {"console": len(console_files), "pc": len(pc_files)},
        "players":      player_summary,
        "console":      console_agg,
        "pc":           pc_agg,
    }
    total_console = sum(v["games"] for v in aggregate["console"]["civWinRates"].values())
    total_pc      = sum(v["games"] for v in aggregate["pc"]["civWinRates"].values())
    print(f"\nConsole total games: {total_console:,}")
    print(f"PC total games:      {total_pc:,}")
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
