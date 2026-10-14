#!/usr/bin/env python3
"""
console_discovery.py - Find console players who are playing right now and add the ones we do not track.

Why: leaderboard_sync.py only runs weekly, so brand-new console accounts are invisible for days, and some
console players sit in players.json under group "pc" (stuck in the huge PC backfill queue). aoe2companion's
live feed lists every game in progress, tagged with its ladder. Any player in a console-ladder game
(rm_1v1_console / rm_team_console) is a console player, so:

  * not in players.json            -> add as group "console" and put at the FRONT of data/backfill_queue.json
  * in players.json as "pc", with NO data file yet -> switch to "console" and move to the front of the queue
  * in players.json as "pc" WITH a data/pc file     -> left alone and noted in the log for a human to look at
    (switching it would orphan the existing file)
  * anything else (console / pro / streamer)       -> untouched

Only console-ladder presence counts, so a PC player who joins a custom game with console players is never added.
Every change is appended to data/console-discovery-log.json (newest last, capped) so it can be reviewed.

Usage:  python3 console_discovery.py [--seconds 20] [--dry-run]
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CONSOLE_LADDERS = {"rm_1v1_console", "rm_team_console"}
SOCKET_URL = "wss://socket.aoe2companion.com/listen?handler=ongoing-matches"
LOG_CAP = 300
REPO = Path(__file__).parent


def extract_console_players(events):
    """{profileId: (name, leaderboardId)} for every real player in a console-ladder game."""
    found = {}
    for e in events:
        if e.get("type") not in ("matchAdded", "matchUpdated"):
            continue
        m = e.get("data") or {}
        if m.get("leaderboardId") not in CONSOLE_LADDERS or m.get("finished"):
            continue
        for p in m.get("players") or []:
            pid = p.get("profileId")
            if isinstance(pid, int) and pid > 0:
                found[pid] = (p.get("name") or f"player_{pid}", m["leaderboardId"])
    return found


def plan_changes(found, players_by_id, has_pc_file):
    """Decide what to do. Returns (adds, regroups, skipped) as lists of (pid, name, ladder)."""
    adds, regroups, skipped = [], [], []
    for pid, (name, ladder) in sorted(found.items()):
        p = players_by_id.get(pid)
        if p is None:
            adds.append((pid, name, ladder))
        elif p.get("group") == "pc":
            (skipped if has_pc_file(pid) else regroups).append((pid, name, ladder))
    return adds, regroups, skipped


def collect_live(seconds):
    """Read the live feed for a few seconds and return all events."""
    import websocket  # pip install websocket-client
    events = []
    ws = websocket.create_connection(SOCKET_URL, timeout=15, header=["User-Agent: AoE2DataPipeline/1.0"])
    ws.settimeout(5)
    end = time.time() + seconds
    try:
        while time.time() < end:
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except websocket.WebSocketConnectionClosedException:
                break
            if not raw:
                break
            try:
                batch = json.loads(raw)
            except ValueError:
                continue
            if isinstance(batch, list):
                events.extend(batch)
    finally:
        try:
            ws.close()
        except Exception:
            pass
    return events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--events-file", help="read events from a JSON file instead of the live feed (testing)")
    args = ap.parse_args()

    if args.events_file:
        events = json.loads(Path(args.events_file).read_text(encoding="utf-8"))
    else:
        try:
            events = collect_live(args.seconds)
        except Exception as e:  # the live feed being down must never fail the workflow
            print(f"Could not read the live feed: {e}")
            return 0
    found = extract_console_players(events)
    print(f"Live feed: {len(events)} events, {len(found)} players in console-ladder games")
    if not found:
        return 0

    players_path = REPO / "players.json"
    players = json.loads(players_path.read_text(encoding="utf-8"))
    by_id = {p["profileId"]: p for p in players}
    pc_dir = REPO / "data" / "pc"
    adds, regroups, skipped = plan_changes(found, by_id, lambda pid: (pc_dir / f"{pid}.json").exists())
    print(f"New: {len(adds)} | PC->console: {len(regroups)} | left alone (has PC data): {len(skipped)}")
    for tag, rows in (("add", adds), ("regroup", regroups), ("skip", skipped)):
        for pid, name, ladder in rows[:30]:
            print(f"  {tag:7} {pid} {name} ({ladder})")
    if args.dry_run or not (adds or regroups or skipped):
        if args.dry_run:
            print("[dry-run] nothing written")
        return 0

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    changed = False
    if adds or regroups:
        for pid, name, _ in adds:
            players.append({"profileId": pid, "name": name, "group": "console"})
        for pid, _, _ in regroups:
            by_id[pid]["group"] = "console"
        players_path.write_text(json.dumps(players, indent=2), encoding="utf-8")
        queue_path = REPO / "data" / "backfill_queue.json"
        queue = json.loads(queue_path.read_text(encoding="utf-8")) if queue_path.exists() else []
        front = [pid for pid, _, _ in adds + regroups]
        front_set = set(front)
        queue_path.write_text(json.dumps(front + [q for q in queue if q not in front_set], indent=2), encoding="utf-8")
        changed = True

    log_path = REPO / "data" / "console-discovery-log.json"
    log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
    known_skips = {(e["profileId"], e["action"]) for e in log}
    for action, rows in (("added", adds), ("regrouped", regroups), ("skipped-has-pc-data", skipped)):
        for pid, name, ladder in rows:
            if action == "skipped-has-pc-data" and (pid, action) in known_skips:
                continue   # note each skipped player once
            log.append({"at": now, "profileId": pid, "name": name, "ladder": ladder, "action": action})
            changed = True
    log_path.write_text(json.dumps(log[-LOG_CAP:], indent=1), encoding="utf-8")
    print("Done." if changed else "No changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
