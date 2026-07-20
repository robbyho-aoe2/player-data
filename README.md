# robbyho-aoe2/player-data

Automated match history store for the [AoE2 Console Civ Lookup tool](https://robbyho-aoe2.github.io/civ-lookup/).

Data is fetched weekly from the [AoE2Companion API](https://data.aoe2companion.com) via GitHub Actions and stored as JSON, one file per player. The civ-lookup tool fetches these files at runtime instead of bundling the data into `index.html`.

## Structure

```
players.json          # seed list: [{name, profileId, group}]
data/
  <profileId>.json    # full match history + meta per player
test_api.py           # one-shot API connectivity/field check
update_players.py     # pipeline script (run by CI, or locally)
.github/workflows/
  update-players.yml  # weekly cron + manual dispatch
```

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
