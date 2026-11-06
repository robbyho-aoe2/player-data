"""Resolve rebase conflicts in the files that every pipeline workflow edits: players.json, data/backfill_queue.json and data/refresh_checked.json.

Why: a backfill batch runs for hours. While it works, Console Discovery (every 30 minutes) appends new players to both files,
so the backfill's own commit no longer rebases cleanly and its push used to fail after hours of work (every run since
2026-10-22). Git's line merge can't know that these files are lists of independent records, so this merges them by record:

  players.json            start from the latest upstream version; apply each record the backfill changed (matched by
                          profileId, field by field); add records only the backfill has
  data/backfill_queue.json  start from the latest upstream queue; drop the ids this backfill processed (the ones it removed
                          from the queue it started with); add ids only the backfill has
  data/refresh_checked.json  a {profileId: "last checked" date} map that Update Player Data and Backfill both stamp; start from the
                          latest upstream map and take the newer date for every id this run stamped (a conflict here failed
                          every Backfill and Update Player Data push from 2026-11-03: "rebase stopped on files merge_shared.py
                          does not handle")

Run it in the middle of a stopped `git rebase origin/main`: stage 1 is the common ancestor, stage 2 the upstream version
and stage 3 the backfill's commit. Anything else still conflicting is left alone (the caller aborts and retries).
"""
import json
import subprocess
import sys


def stage(n, path):
    r = subprocess.run(["git", "show", f":{n}:{path}"], capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None


def merge_players(base, upstream, ours):
    by_id = {p["profileId"]: dict(p) for p in upstream}
    order = [p["profileId"] for p in upstream]
    base_by = {p["profileId"]: p for p in (base or [])}
    for p in ours:
        pid = p["profileId"]
        if pid not in by_id:
            if pid not in base_by:          # new in the backfill; (if it was in base and upstream dropped it, leave it dropped)
                by_id[pid] = dict(p)
                order.append(pid)
            continue
        old = base_by.get(pid, {})
        for k, v in p.items():
            if old.get(k) != v:             # a field the backfill changed
                by_id[pid][k] = v
    return [by_id[i] for i in order]


def merge_queue(base, upstream, ours):
    base_set, ours_set = set(base or []), set(ours)
    removed = base_set - ours_set           # processed by this backfill
    added = [x for x in ours if x not in base_set]
    out = [x for x in upstream if x not in removed]
    have = set(out)
    out += [x for x in added if x not in have]
    return out


def merge_checked(base, upstream, ours):
    base = base or {}
    out = dict(upstream)
    for k, v in ours.items():
        if base.get(k) != v:                # an id this run stamped
            if k not in out or str(v) > str(out[k]):    # ISO dates, so the string order is the date order
                out[k] = v
    return out


def main():
    conflicted = subprocess.run(["git", "diff", "--name-only", "--diff-filter=U"], capture_output=True, text=True).stdout.split()
    done = []
    for path, fn in (("players.json", merge_players), ("data/backfill_queue.json", merge_queue),
                     ("data/refresh_checked.json", merge_checked)):
        if path not in conflicted:
            continue
        base, upstream, ours = stage(1, path), stage(2, path), stage(3, path)
        if upstream is None or ours is None:
            print(f"cannot read the versions of {path}", file=sys.stderr)
            return 1
        merged = fn(base, upstream, ours)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(merged, f, separators=(",", ":")) if path == "data/refresh_checked.json" else json.dump(merged, f, indent=2)
        subprocess.run(["git", "add", path], check=True)
        done.append(path)
        print(f"merged {path}: {len(upstream)} upstream + this run's changes -> {len(merged)} records")
    print("resolved:", done or "nothing (no conflict in the shared files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
