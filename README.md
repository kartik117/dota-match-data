# Dota Match Data Sync

Public OpenDota data for Steam32 account **467233306** in [kartik117/dota-match-data](https://github.com/kartik117/dota-match-data). The existing six-hour schedule remains **00:17, 06:17, 12:17, 18:17 UTC** (`17 */6 * * *`); GitHub can delay schedules. Manual runs are available under **Actions → Sync OpenDota matches → Run workflow**.

## Public data

- [Latest 50 ranked analysis](https://raw.githubusercontent.com/kartik117/dota-match-data/main/data/analysis/last50.json)
- [Latest 100 matches, any lobby](https://raw.githubusercontent.com/kartik117/dota-match-data/main/data/matches.json)
- [Latest 50 ranked history](https://raw.githubusercontent.com/kartik117/dota-match-data/main/data/ranked_matches.json)
- [Sync summary and parsing status](https://raw.githubusercontent.com/kartik117/dota-match-data/main/data/summary.json)
- Detail cache: `https://raw.githubusercontent.com/kartik117/dota-match-data/main/data/matches/<match_id>.json`

Ranked selection uses a separate server-filtered `lobby_type=7&limit=50` request. It does not select ranked matches only from the latest 100 all-lobby matches. If fewer than 50 are returned, the analysis reports the available count explicitly.

## Analysis schema and interpretation

`data/analysis/last50.json` is compact JSON with per-match statistics, aggregate means and sample counts, win/loss means and differences, and per-hero aggregates and comparisons. Heroes use OpenDota's numeric hero IDs.

- Per-match `metrics` include outcome, hero, duration, kills/deaths/assists, KDA ratio, GPM/XPM, last hits/denies, final net worth, lane efficiency, hero/tower damage, teamfight participation, lane/farming/stack counts and first observed purchase time for each item.
- `progression` contains exact `[seconds,value]` observations of cumulative gold, XP, last hits, denies and **net worth**. Cumulative gold is never substituted for net worth.
- `snapshots_minutes` contains exact observations at 10, 20 and 30 minutes. Matches that end earlier, missing samples, and misaligned timelines produce unavailable values; no interpolation is used.
- `events` retains item purchases, kill/death timestamps, buybacks, objectives, Roshan/Aegis events and the player's data within detected teamfights. Objectives and Roshan events are match-wide context, not automatic player credit. Purchase time does not guarantee item completion.
- `positioning` retains available lane positions and observer/sentry ward heatmaps, not an invented full movement trace.

Values use `{ "value": ..., "n": 0 or 1 }` per match. **Unavailable = `null`, `n=0`**; an observed zero or empty event log has `n=1`. Aggregate `n` counts matches with observed data, not event or timeline-point counts. Win/loss differences include both sample counts. `field_sample_counts` covers timelines, event logs and positioning. Item timing means include only matches with an observed purchase of that item. Raw K/D/A remains available for deathless games, but the divide-by-zero KDA ratio is null. Duration/event times are in seconds; lane efficiency is percent and participation is a fraction. The participation scalar is OpenDota's provided metric; the detected teamfight records are separately available.

Comparisons are **descriptive correlations**, not proof of mistakes or causal effects. Hero, role, patch, duration, opponent quality and selection effects are not controlled. Missing replay data can bias samples. Shorter matches are excluded from later minute snapshots, so those comparison groups can differ.

## Replay coverage

Each match reports one of `full_replay`, `partial_replay`, `basic_only`, `history_only`, plus its parsed flag and missing core fields. The analysis `coverage` reports all category counts.

A positive OpenDota parsed flag/version alone is not called complete. `full_replay` additionally requires aligned gold/XP/LH/denies/net-worth timelines, purchase/kill/death/buyback logs, lane positions, lane efficiency, participation, objectives and teamfights. This is a documented core coverage definition, not a claim that every possible replay field is present. Available fields are used even for partial matches. Optional fields still report their own sample counts.

## Caching and parsing requests

All existing detail files are retained permanently; there is no cache pruning. Complete cached replay details are never downloaded again unless `FORCE_REFRESH=true`. Basic or partial responses are checked at most daily for recent matches and weekly for older matches. Failed downloads have a six-hour cooldown. The richer parsed cached response is preserved if an API response becomes unparsed. `data/sync_state.json` persists checks, cooldowns and parse submissions, including jobs whose response may have been lost. It contains no credentials.

Parsing is enabled by `REQUEST_PARSING=true`, with `MAX_PARSE_REQUESTS=3` (hard cap 3 per run). Only unparsed matches with a replay URL and a match end within a conservative seven-day window are candidates. This window is a local policy, not a promise of replay availability: OpenDota and Valve decide eligibility. Older matches keep their existing basic data and can still be checked for archived parsing on the weekly cooldown.

The official [OpenDota API implementation/specification](https://github.com/odota/core/blob/master/svc/api/spec.ts) documents `POST /request/{match_id}` and says it costs **ten calls** for rate limiting. POSTs are spaced by 12 seconds, attempted only once per submission, and recorded **before** sending so a timeout cannot cause an immediate duplicate. Each match has a 48-hour resubmission cooldown and a maximum of three submissions. Returned job IDs and submission outcomes appear in the state/summary. A queued job does not mean parsing succeeded. Match details are checked again after six hours; subsequent parsed data is included automatically.

No API key or OpenDota/Steam login is required by this client. If the service requires authorization, rejects a match, or rate-limits requests, the pipeline records the failure and does not bypass the restriction. HTTP 401/403 and exhausted 429 responses suspend further API calls during that run. Ordinary GETs are paced by 1.3 seconds, with bounded retries for transient errors and `Retry-After` support; long cooldowns defer to the next run. Parsing availability is not guaranteed.

Failed, empty or invalid history retrieval stops before existing match data is changed. JSON files are replaced atomically. GitHub Actions serializes runs, tests error handling and analysis, validates the analysis by recomputing it from the cached raw data, then commits updates with its scoped `contents: write` token.

## Local use

Python 3.12+, standard library only:

```sh
python3 -m unittest discover -s tests -v
python3 -u scripts/fetch.py
python3 scripts/validate.py
```

`fetch.py` also generates the analysis. To regenerate it without API calls, run `python3 scripts/analysis.py`. Configuration: `DOTA_ACCOUNT_ID=467233306`, `MATCH_COUNT=100`, `DETAIL_COUNT=50`, `REQUEST_PARSING=true`, `MAX_PARSE_REQUESTS=3`, `FORCE_REFRESH=false`. The workflow sets the first five explicitly. No existing data files are deleted.
