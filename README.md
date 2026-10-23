# Dota Match Data Sync

Public OpenDota data for Steam32 account **467233306**, maintained at [kartik117/dota-match-data](https://github.com/kartik117/dota-match-data).

The sync fetches the latest **100 matches** and details for the latest **12 matches of any lobby type**. GitHub Actions runs every six hours at **00:17, 06:17, 12:17, and 18:17 UTC** (`17 */6 * * *`). GitHub may delay scheduled runs. You can also use **Actions → Sync OpenDota matches → Run workflow**.

## Public JSON

- [Match history](https://raw.githubusercontent.com/kartik117/dota-match-data/main/data/matches.json): latest 100 matches with results, heroes, K/D/A, duration, and start times.
- [Sync summary](https://raw.githubusercontent.com/kartik117/dota-match-data/main/data/summary.json): account, UTC update time, counts, recent results, and per-match availability and parsing status.
- Match details: `https://raw.githubusercontent.com/kartik117/dota-match-data/main/data/matches/<match_id>.json` for each selected match ID listed in `detail_status` in the summary.

`recent_ranked` remains available in the summary as a convenience, but detail selection uses the newest matches regardless of lobby type.

## Reliability and parsing

The script retries network errors, invalid JSON, HTTP 408/429, and common server errors with backoff. It honors `Retry-After` values in seconds or HTTP-date format. A server cooldown longer than 120 seconds defers that request until a later sync. Permanent errors such as unavailable matches do not waste retries. Requests are paced at 1.3 seconds apart.

Failed, empty, or malformed history responses stop the job before existing data is changed. Individual unavailable details are reported without breaking the sync; a valid cached copy is retained when available. Each JSON file is written atomically. Detail responses must contain the correct match ID and this player's account ID. Old detail files are removed after successful history retrieval so the directory contains only the selected latest 12.

OpenDota can return valid basic match details without replay-parsed timelines. These responses are saved with `parsed: false` in the summary. The sync does not request replay parsing or fabricate missing fields. It retries these matches on future runs and preserves richer parsed cached data if the API later returns an unparsed copy. `source: cache` and `updated: false` distinguish retained data from a fresh API response.

`detail_count_updated`, `detail_count_available`, and `detail_count_parsed` distinguish fresh downloads, available files, and parsed files. Missing details are explicitly listed with `available: false`; their raw file URL will not exist until data becomes available.

## Local use and checks

Python 3.12+; no third-party packages or OpenDota API key required:

```sh
python3 -m unittest discover -s tests -v
python3 -u scripts/fetch.py
python3 scripts/validate.py
```

Configuration uses `DOTA_ACCOUNT_ID`, `MATCH_COUNT`, and `DETAIL_COUNT`, set explicitly in the workflow. The defaults are 467233306, 100, and 12. Counts must satisfy `1 <= DETAIL_COUNT <= MATCH_COUNT <= 100`.

The workflow runs the error-handling tests, fetches data, validates match IDs, player membership, counts and parsing status, and commits changes using its scoped `contents: write` GitHub token. Runs are serialized to avoid conflicting pushes. No personal tokens are stored in this repository.
