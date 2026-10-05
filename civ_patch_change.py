#!/usr/bin/env python3
"""
civ_patch_change.py - civ win rate before vs. after a balance patch, console only.

Powers the "Civ Win Rate Change Since the Viking Sagas Update" section on insights.html
and the per-civ game counts in civ-insights.html's Sept 22 changelog.

Every match in data/console/*.json carries its own `patch` number, so the split is exact
(no date boundary): matches on BEFORE_PATCH (1800 - the whole previous balance patch,
Jul 7 - Sep 22, 2026) are "before", matches on AFTER_PATCH_MIN or later (1815+, the DLC
patch) are "after". Both ladders are kept separate (1v1 Console / Team Console). Void or
disconnected matches are skipped with weekly_extras.is_void_match(), same as the weekly
report. Win rates are raw (not rating-adjusted).

Output (data/civ-patch-change.json):
  {"generatedAt", "through", "beforePatch", "afterPatchMin", "beforeRange": [first, last],
   "lowSampleBelow": 40,
   "1v1 Console": {"before": [games, wr%], "after": [games, wr%],
                   "rows":    [[civ, wrBefore%, gamesBefore, wrAfter%, gamesAfter], ...],
                   "newOnly": [[civ, null, gamesBefore, wrAfter%, gamesAfter], ...]},
   "Team Console": {...}}
Every civ with at least one game after the patch is listed. "lowSampleBelow" (MIN_AFTER)
tells the site which ones have too few games after the patch to trust, so it can flag them;
a civ with fewer than MIN_BEFORE games before the patch goes in "newOnly" (the three Viking
Sagas civs have none).

Usage:  python3 civ_patch_change.py [--out data/civ-patch-change.json]
        [--before-patch 1800] [--after-patch-min 1815]
"""
import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import weekly_extras as we  # noqa: E402

LADDERS = ("1v1 Console", "Team Console")
MIN_AFTER = 40
MIN_BEFORE = 40


def compute(players, before_patch, after_patch_min):
    out = {}
    through = ""
    before_dates = []
    for lad in LADDERS:
        before = defaultdict(lambda: {"g": 0, "w": 0})
        after = defaultdict(lambda: {"g": 0, "w": 0})
        for p in players:
            for m in p.get("ladders", {}).get(lad, {}).get("matches", []):
                d, civ, patch = m.get("date"), m.get("civ"), m.get("patch")
                if not d or not civ or patch is None or we.is_void_match(m):
                    continue
                if patch == before_patch:
                    tgt = before
                    before_dates.append(d)
                elif patch >= after_patch_min:
                    tgt = after
                    through = max(through, d)
                else:
                    continue
                tgt[civ]["g"] += 1
                tgt[civ]["w"] += 1 if m.get("won") else 0
        tb = sum(v["g"] for v in before.values())
        ta = sum(v["g"] for v in after.values())
        rows, new_only = [], []
        for civ in sorted(set(before) | set(after)):
            a, b = before.get(civ, {"g": 0, "w": 0}), after.get(civ, {"g": 0, "w": 0})
            if b["g"] == 0:
                continue
            wr_after = round(b["w"] / b["g"] * 100, 1)
            if a["g"] < MIN_BEFORE:
                new_only.append([civ, None, a["g"], wr_after, b["g"]])
            else:
                rows.append([civ, round(a["w"] / a["g"] * 100, 1), a["g"], wr_after, b["g"]])
        out[lad] = {
            "before": [tb, round(sum(v["w"] for v in before.values()) / tb * 100, 1) if tb else None],
            "after": [ta, round(sum(v["w"] for v in after.values()) / ta * 100, 1) if ta else None],
            "rows": rows,
            "newOnly": new_only,
        }
    return out, through, (min(before_dates), max(before_dates)) if before_dates else (None, None)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default="data/console")
    ap.add_argument("--out", default="data/civ-patch-change.json")
    ap.add_argument("--before-patch", type=int, default=1800)
    ap.add_argument("--after-patch-min", type=int, default=1815)
    args = ap.parse_args()

    players = list(we.load_players(args.data_dir))
    ladders, through, (first, last) = compute(players, args.before_patch, args.after_patch_min)
    result = {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "through": through,
        "beforePatch": args.before_patch,
        "afterPatchMin": args.after_patch_min,
        "beforeRange": [first, last],
        "lowSampleBelow": MIN_AFTER,
        **ladders,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    for lad in LADDERS:
        r = result[lad]
        print(f"{lad}: before {r['before'][0]:,} games ({r['before'][1]}%), after {r['after'][0]:,} ({r['after'][1]}%), "
              f"{len(r['rows'])} civs + {len(r['newOnly'])} new-only")
    print(f"through {through}; before range {first} .. {last}; wrote {args.out}")


if __name__ == "__main__":
    main()
