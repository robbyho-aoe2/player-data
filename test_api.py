#!/usr/bin/env python3
"""
test_api.py — One-shot test to verify aoe2companion API is reachable
from a server/GitHub Actions context and that the response has the
fields our pipeline expects.

Run: python3 test_api.py
"""

import json
import sys
import urllib.request
import urllib.parse

BASE_URL = "https://data.aoe2companion.com/api/matches"
TEST_PROFILE_ID = 13648083  # BORJA_GZ80 — known high-volume console player

EXPECTED_MATCH_FIELDS = {"matchId", "leaderboardName", "started", "finished"}
EXPECTED_PLAYER_FIELDS = {"profileId", "rating", "won"}
CIV_FIELD_CANDIDATES = {"civName", "civilizationName", "civ"}
MAP_FIELD_CANDIDATES = {"mapType", "mapName", "map"}


def fetch(profile_id: int, page: int = 1) -> list:
    params = urllib.parse.urlencode({"profile_ids": profile_id, "page": page})
    url = f"{BASE_URL}?{params}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "AoE2DataPipeline/1.0 (github.com/robbyho-aoe2)"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def check_fields(match: dict, profile_id: int) -> dict[str, str]:
    """Return a report of which expected fields are present/missing."""
    report = {}

    # Top-level match fields
    for f in EXPECTED_MATCH_FIELDS:
        report[f"match.{f}"] = "✓" if f in match else "MISSING"

    # Civ field — API may use different names; find whichever is present
    civ_found = next((f for f in CIV_FIELD_CANDIDATES if f in match), None)
    report["match.civField"] = f"✓ ({civ_found} = {match.get(civ_found, '?')})" if civ_found else "MISSING — none of: " + str(CIV_FIELD_CANDIDATES)

    # Map field
    map_found = next((f for f in MAP_FIELD_CANDIDATES if f in match), None)
    report["match.mapField"] = f"✓ ({map_found} = {match.get(map_found, '?')})" if map_found else "MISSING — none of: " + str(MAP_FIELD_CANDIDATES)

    # Per-player object
    players = match.get("players", [])
    our_player = next((p for p in players if p.get("profileId") == profile_id), None)
    if our_player is None:
        report["player_object"] = "MISSING — not found in players list"
    else:
        for f in EXPECTED_PLAYER_FIELDS:
            report[f"player.{f}"] = "✓" if f in our_player else "MISSING"

    # patch / version field
    patch_candidates = ["version", "patch", "gameVersion"]
    patch_found = next((f for f in patch_candidates if f in match), None)
    report["match.patchField"] = f"✓ ({patch_found} = {match.get(patch_found, '?')})" if patch_found else "MISSING — none of: " + str(patch_candidates)

    return report


def duration_from_match(match: dict) -> int | None:
    s = match.get("started")
    f = match.get("finished")
    if not s or not f:
        return None
    from datetime import datetime, timezone
    fmt = "%Y-%m-%dT%H:%M:%S.%fZ"
    try:
        start_dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        end_dt   = datetime.strptime(f, fmt).replace(tzinfo=timezone.utc)
        return int((end_dt - start_dt).total_seconds())
    except ValueError:
        # Try without microseconds
        fmt2 = "%Y-%m-%dT%H:%M:%SZ"
        try:
            start_dt = datetime.strptime(s, fmt2).replace(tzinfo=timezone.utc)
            end_dt   = datetime.strptime(f, fmt2).replace(tzinfo=timezone.utc)
            return int((end_dt - start_dt).total_seconds())
        except ValueError:
            return None


def main():
    print(f"=== AoE2Companion API Test ===")
    print(f"Target: {BASE_URL}")
    print(f"Profile: {TEST_PROFILE_ID} (BORJA_GZ80)\n")

    try:
        matches = fetch(TEST_PROFILE_ID, page=1)
    except Exception as e:
        print(f"FAIL — Could not reach API: {type(e).__name__}: {e}")
        print("\nFallback required: browser-push workaround needed.")
        sys.exit(1)

    if isinstance(matches, dict) and "matches" in matches:
        matches = matches["matches"]
    if not isinstance(matches, list):
        print(f"FAIL — Unexpected response type: {type(matches)}")
        print(f"Response: {json.dumps(matches)[:300]}")
        sys.exit(1)

    print(f"OK — Got {len(matches)} matches on page 1\n")

    if not matches:
        print("WARNING — Empty match list. Player may have no games or API pagination changed.")
        sys.exit(0)

    # Inspect first match
    m0 = matches[0]
    print("--- First match (raw keys at top level) ---")
    print(", ".join(sorted(m0.keys())))

    if "players" in m0 and m0["players"]:
        print("\n--- First player object keys ---")
        print(", ".join(sorted(m0["players"][0].keys())))

    print("\n--- Field check ---")
    report = check_fields(m0, TEST_PROFILE_ID)
    all_ok = True
    for field, status in sorted(report.items()):
        icon = "  " if status.startswith("✓") else "!!"
        print(f"  {icon} {field}: {status}")
        if "MISSING" in status:
            all_ok = False

    # Duration test
    dur = duration_from_match(m0)
    print(f"\n--- Duration computation ---")
    print(f"  started:  {m0.get('started', 'N/A')}")
    print(f"  finished: {m0.get('finished', 'N/A')}")
    print(f"  computed: {dur}s ({dur//60}m {dur%60}s)" if dur is not None else "  computed: FAILED")

    # Ladder name test
    lb = m0.get("leaderboardName", "N/A")
    LADDER_MAP = {
        "1v1 Random Map (Console)":  "1v1 Console",
        "1v1 Random Map (Controller)": "1v1 Console",
        "Team Random Map (Console)": "Team Console",
        "Team Random Map (Controller)": "Team Console",
        "1v1 Random Map":            "1v1 PC",
        "Team Random Map":           "Team PC",
    }
    mapped = LADDER_MAP.get(lb, "UNKNOWN — add to LADDER_MAP")
    print(f"\n--- Ladder mapping ---")
    print(f"  leaderboardName: {lb!r}")
    print(f"  → internal bucket: {mapped}")

    print(f"\n{'=== ALL CHECKS PASSED ===' if all_ok else '=== SOME FIELDS MISSING — review above ==='}")
    print("\nFull first match JSON:")
    print(json.dumps(m0, indent=2)[:1500])

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
