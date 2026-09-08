# robbyho-aoe2/player-data

Automated match history store for the [AoE2 Console Civ Lookup tool](https://robbyho-aoe2.github.io/civ-lookup/).

Data is fetched weekly from the [AoE2Companion API](https://data.aoe2companion.com) via GitHub Actions and stored as JSON, one file per player. The civ-lookup tool fetches these files at runtime instead of bundling the data into `index.html`.


## Structure

```
players.json          # seed list: [{name, profileId, group}]
data/
  <profileId>.json         # full match history + meta per player
  refresh_cursor.json      # rotation position for the batch scheduler below
  pipeline_health.json     # per-run freshness snapshot, see below
test_api.py            # one-shot API connectivity/field check
update_players.py      # pipeline script (run by CI, or locally)
write_pipeline_health.py  # writes data/pipeline_health.json each run
.github/workflows/
  update-players.yml  # weekly cron + manual dispatch
```

## Pipeline health

Every `update-players.yml` run writes `data/pipeline_health.json`, a small
snapshot answering "can I trust this week's numbers right now?": when the
pipeline last ran, how many of the tracked (already-backfilled) players have
data pulled within the last 7 days, and what fraction that is
(`freshWithin7DaysPct`). Refresh runs rotate through the tracked pool in
batches (`data/refresh_cursor.json` + `BATCH_SIZE`), so freshness lags behind
"just ran" by design — this file exists so anyone consuming this data
(notably the external weekly-report automation that reads/writes
`data/weekly-reports/` and `data/snapshots/` in this repo) can check
`freshWithin7DaysPct` before trusting a week's totals, instead of relying
only on a report's own after-the-fact heuristics (e.g. "this total looks
implausibly low").


## Per-player file schema

```json
{
  "name": "BORJA_GZ80",
  "profileId": 13648083,
  "group": "console",
  "ladders": {
    "1v1 Console": {
      "meta": { "totalGames": 3575, "latestRating": 1017, "pulledDate": "2026-07-19" },
      "matches": [
        { "matchId": 493201041, "civ": "Franks", "map": "Arabia", "won": true,
          "patch": 162286, "date": "2026-07-18", "dur": 1830 }
      ]
    },
    "Team Console": { "meta": {}, "matches": [] },
    "1v1 PC":       { "meta": {}, "matches": [] },
    "Team PC":      { "meta": {}, "matches": [] }
  }
}
```

Matches are stored newest-first. `matchId` is the dedup key — never dedup by `{civ, map, won, date}` (not unique for high-volume players).

**Void/disconnected matches:** `won` (and usually `dur`) can be `null` on one
side's copy of a matchId while the opponent's copy has a real result —
confirmed by cross-checking several sampled pairs, the null side never had a
recorded outcome at all (not a draw, not in-progress by the time of the pull).
This looks like one side's client never reporting a result, e.g. a
disconnect. `None` is falsy in Python, so `if m.get("won"):` silently treats
a void match as a loss instead of skipping it — always check
`m.get("won") is None` explicitly first (see `is_void_match()` in
`weekly_extras.py`) before trusting a match's outcome. As of 2026-09-08 this
affected ~163 matches in a single 5-day console window, so it's common
enough to matter, not an edge case.

## Weekly report conventions

`weekly_extras.py` is the source of truth for how the window-scoped parts
of the console weekly report are computed — read it (or run it with
`--help`) rather than re-deriving the definitions:

- **mostGames** (`compute_most_games`): combined 1v1 + Team Console games
  this window, with each player's finishing (current) Elo and net change
  per ladder. `elo1v1Change` diffs against `data/snapshots/console-<windowStart>.json`
  (pass its path as `--snapshot-start`); `eloTeamChange` is null until a
  `ratingTeam` field exists in two consecutive weekly snapshots — see below.
- **biggestUpsets** (`compute_biggest_upsets`): current-rating-as-proxy
  upsets, void matches excluded.
- **civPopularity** (`compute_civ_popularity`): `topPicked`/`topWinRate` now
  carry `pickRate` (share of this window's civ-picks, same definition as
  `build_aggregate.py`'s `pickRate`); `biggestMovers` is sorted by *signed*
  `pctPointChange` (gainers first, losers last), not by magnitude.
- **topSquads** (`compute_squads` with `--roster-only`): restricted to
  console-roster teammates only, matching `snl_report.py`'s ratstacks —
  pass `roster_ids` (or `--roster-only` on the CLI) rather than the old
  any-teammate behavior.

**Team-rating snapshot bootstrap:** `data/snapshots/console-<date>.json` has
historically only ever recorded `rating1v1` per player, so `eloTeamChange`
has no baseline yet. `weekly_extras.py --window-start ... --window-end ...`
now also emits `teamRatingSnapshot` (`{profileId: ratingTeam}`) — merge that
into that day's `data/snapshots/console-<date>.json` as a `ratingTeam` field
per player (alongside the existing `rating1v1`) every week from here on.
Team Elo deltas become available starting the week *after* two consecutive
snapshots carry `ratingTeam`.

## Triggering manually

Go to **Actions → Update Player Data → Run workflow**. Optionally specify:
- `player_id`: a single `profileId` to update (default: all players)
- `pages`: pages to fetch per player (default: 5 = ~100 matches)
- `dry_run`: `true` to fetch without writing files

## Adding players

Add an entry to `players.json`:
```json
{ "name": "DisplayName", "profileId": 12345678, "group": "console" }
```

The next Action run will create `data/12345678.json` automatically.

## Civ name normalization

| API returns | Stored as |
|-------------|-----------|
| `Mayans`    | `Maya`    |
| `Inca`      | `Incas`   |

Add additional mappings to `CIV_NORM` in `update_players.py` as needed.
