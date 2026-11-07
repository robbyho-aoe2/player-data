#!/usr/bin/env python3
"""Regression tests for the weekly-report window-boundary logic.

Written after a real bug: in_window() used to be fully inclusive
[start, end], but a report's windowStart is always the PRIOR week's own
windowEnd (the shared snapshot date) — so a match dated exactly on that
boundary landed in both weeks' reports (confirmed: the 2026-09-21
broosethegreat/Chiefboy AK match appeared in both the 09-21 and 09-28
biggestUpsets). Fixed to half-open (start, end]; these tests exist so
that fix can't silently regress.

Run directly: `python3 test_weekly_extras.py`. Exits non-zero on any
failure, so it's suitable as a CI gate (see .github/workflows/tests.yml).
No pytest dependency — plain asserts, matching this repo's other scripts.
"""
import json
import sys
from pathlib import Path

import weekly_extras as we
import rivalries as riv

REPO = Path(__file__).resolve().parent
FAILURES = []


def check(label, condition):
    status = "ok" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        FAILURES.append(label)


def test_in_window_boundary_semantics():
    print("in_window(): half-open (start, end] boundary semantics")
    start, end = "2026-09-14", "2026-09-21"
    check("date == start is EXCLUDED (belongs to the prior week)", we.in_window(start, start, end) is False)
    check("date == end is INCLUDED", we.in_window(end, start, end) is True)
    check("date strictly between is INCLUDED", we.in_window("2026-09-18", start, end) is True)
    check("date before start is EXCLUDED", we.in_window("2026-09-13", start, end) is False)
    check("date after end is EXCLUDED", we.in_window("2026-09-22", start, end) is False)
    check("falsy date is EXCLUDED", we.in_window(None, start, end) is False)
    check("empty-string date is EXCLUDED", we.in_window("", start, end) is False)


def test_consecutive_windows_dont_overlap():
    print("Two consecutive weekly windows never both claim the shared boundary date")
    prior_start, prior_end = "2026-09-07", "2026-09-14"
    this_start, this_end = "2026-09-14", "2026-09-21"  # this_start == prior_end, by construction
    boundary_date = prior_end
    in_prior = we.in_window(boundary_date, prior_start, prior_end)
    in_this = we.in_window(boundary_date, this_start, this_end)
    check("boundary date claimed by exactly one of the two windows", in_prior != in_this)
    check("...specifically, claimed by the week that ENDS on it", in_prior is True and in_this is False)


def test_rivalries_window_filter_matches_in_window():
    print("rivalries.py's inline window filter uses the same half-open semantics")
    pairs = {(1, 2): [
        {"matchId": 1, "date": "2026-09-14", "map": "Arabia", "selfId": 1, "selfName": "A", "oppId": 2, "oppName": "B", "selfWon": True},
        {"matchId": 2, "date": "2026-09-14", "map": "Arabia", "selfId": 1, "selfName": "A", "oppId": 2, "oppName": "B", "selfWon": True},
        {"matchId": 3, "date": "2026-09-14", "map": "Arabia", "selfId": 1, "selfName": "A", "oppId": 2, "oppName": "B", "selfWon": True},
        {"matchId": 4, "date": "2026-09-18", "map": "Arabia", "selfId": 1, "selfName": "A", "oppId": 2, "oppName": "B", "selfWon": False},
    ]}
    rows = riv.build_rivalries(pairs, min_games=3, top=10, window_start="2026-09-14", window_end="2026-09-21")
    check("3 matches dated exactly on window_start are excluded from gamesThisWeek", rows[0]["gamesThisWeek"] == 1)


def test_archived_reports_dont_share_matchids_across_weeks():
    """The actual symptom that surfaced this bug: the same match appearing
    in two consecutive weeks' biggestUpsets. Runs against the real archive
    so it catches a real regression, not just a synthetic one."""
    print("Archived weekly reports: no matchId repeats across consecutive weeks' biggestUpsets")
    reports_dir = REPO / "data" / "weekly-reports"
    if not reports_dir.exists():
        print("  [skip] no data/weekly-reports directory (fresh checkout without data?)")
        return
    index_path = reports_dir / "index.json"
    if not index_path.exists():
        print("  [skip] no index.json yet")
        return
    dates = json.loads(index_path.read_text())  # newest first
    dates = list(reversed(dates))  # chronological
    prev_ids = None
    prev_date = None
    for date in dates:
        report_path = reports_dir / f"console-{date}.json"
        if not report_path.exists():
            continue
        report = json.loads(report_path.read_text())
        ids = {u.get("matchId") for u in report.get("biggestUpsets", []) if u.get("matchId") is not None}
        if prev_ids is not None and ids and prev_ids:
            overlap = ids & prev_ids
            check(f"{prev_date} -> {date}: no shared upset matchId", not overlap)
        prev_ids, prev_date = ids, date

    # Policy: every report must reflect exactly 1 week -- windowStart must
    # chain from the immediately preceding report's windowEnd (no gap, no
    # overlap), and that gives a 7-day span. The one allowed exception is
    # the very first report in the archive, which is bounded by whenever
    # snapshot tracking actually began, not by a missing predecessor.
    # This used to be a soft "note" (a gap looked like a different bug than
    # the overlap this suite was written for) -- it isn't: 09-07 shipped a
    # 4-day window instead of 7 because it read windowStart from whatever
    # snapshot existed rather than from the actual preceding report, and
    # silently dropped ~2,400 games nobody's report ever counted. Fixed
    # 2026-09-28; this is now a hard check so a future report can't repeat
    # either failure mode (gap or overlap) silently.
    import datetime
    prev_end = None
    for i, date in enumerate(dates):
        report_path = reports_dir / f"console-{date}.json"
        if not report_path.exists():
            continue
        report = json.loads(report_path.read_text())
        if prev_end is not None:
            check(f"{date}: windowStart ({report['windowStart']}) chains from prior windowEnd ({prev_end})",
                  report["windowStart"] == prev_end)
            span = (datetime.date.fromisoformat(report["windowEnd"]) - datetime.date.fromisoformat(report["windowStart"])).days
            check(f"{date}: window spans exactly 7 days (got {span})", span == 7)
        prev_end = report["windowEnd"]


def main():
    test_in_window_boundary_semantics()
    test_consecutive_windows_dont_overlap()
    test_rivalries_window_filter_matches_in_window()
    test_archived_reports_dont_share_matchids_across_weeks()

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s) failed:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
