#!/usr/bin/env python3
"""Fills in the "most customs" fields of a console weekly report that was published without them.

The Console Weekly Report routine runs in a sandbox that aoe2companion sometimes refuses (HTTP 403 through the proxy), so
weekly_customs.py fails there and the report goes out without mostCustoms / totalCustomGames / playersWithCustoms.
GitHub Actions can reach the API, so weekly-customs.yml runs this a little after the routine and patches the report.

By default it works on the newest report in data/weekly-reports/index.json and does nothing if that report already has
the fields. It runs weekly_customs.py for the report's own window (windowStart, windowEnd), then adds the three keys at
the end of the report, which is where the routine puts them, leaving everything else byte-for-byte as it was.

Usage:
    python3 patch_weekly_customs.py [--date YYYY-MM-DD] [--reports-dir data/weekly-reports] [--players players.json]
                                    [--customs-json FILE]     # use a saved weekly_customs.py output instead of calling the API
"""
import argparse
import json
import os
import subprocess
import sys

KEYS = ("mostCustoms", "totalCustomGames", "playersWithCustoms")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", help="report date to patch (default: the newest in index.json)")
    ap.add_argument("--reports-dir", default="data/weekly-reports")
    ap.add_argument("--players", default="players.json")
    ap.add_argument("--customs-json", help="saved output of weekly_customs.py (skips the API)")
    args = ap.parse_args()

    index = json.load(open(os.path.join(args.reports_dir, "index.json"), encoding="utf-8"))
    date = args.date or index[0]
    path = os.path.join(args.reports_dir, f"console-{date}.json")
    raw = open(path, encoding="utf-8", newline="").read()
    report = json.loads(raw)
    if all(k in report for k in KEYS):
        print(f"{date}: already has the custom-game fields, nothing to do")
        return 0

    if args.customs_json:
        customs = json.load(open(args.customs_json, encoding="utf-8"))
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        cmd = [sys.executable, os.path.join(here, "weekly_customs.py"), "--players", args.players,
               "--window-start", report["windowStart"], "--window-end", report["windowEnd"], "--top", "5"]
        print(f"{date}: running weekly_customs.py for {report['windowStart']} to {report['windowEnd']}", flush=True)
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        sys.stderr.write(res.stderr[-2000:])
        if res.returncode != 0:
            print(f"{date}: weekly_customs.py failed (exit {res.returncode}); leaving the report as it is")
            return 1
        customs = json.loads(res.stdout)

    for k in KEYS:
        report[k] = customs[k]
    # the routine writes compact JSON (ensure_ascii on); keep that so the diff is only the three new keys
    trailing = "\n" if raw.endswith("\n") else ""
    open(path, "w", encoding="utf-8", newline="").write(json.dumps(report) + trailing)
    print(f"{date}: added mostCustoms ({len(customs['mostCustoms'])} players), "
          f"{customs['totalCustomGames']} custom games, {customs['playersWithCustoms']} players")
    return 0


if __name__ == "__main__":
    sys.exit(main())
