#!/usr/bin/env python3
"""Sync public OpenDota data; Python standard library only."""
import http.client
import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

ACCOUNT = os.environ.get('DOTA_ACCOUNT_ID', '467233306')
MATCH_COUNT = int(os.environ.get('MATCH_COUNT', '100'))
DETAIL_COUNT = int(os.environ.get('DETAIL_COUNT', '12'))
OUT = pathlib.Path('data')
BASE = 'https://api.opendota.com/api'
ATTEMPTS = 4


def retry_delay(attempt, retry_after=None):
    if retry_after:
        try:
            return max(0, float(retry_after))
        except ValueError:
            try:
                return max(0, (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
    return min(60, 5 * 2 ** attempt)


def fetch(path):
    for attempt in range(ATTEMPTS):
        delay = retry_delay(attempt)
        try:
            req = urllib.request.Request(BASE + path, headers={
                'User-Agent': 'dota-match-data-sync/1.0', 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=45) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            print(f'HTTP {exc.code} fetching {path}', flush=True)
            if exc.code not in (408, 429, 500, 502, 503, 504):
                exc.close()
                return None
            delay = retry_delay(attempt, exc.headers.get('Retry-After'))
            exc.close()
        except (urllib.error.URLError, TimeoutError, ValueError, OSError, http.client.HTTPException) as exc:
            print(f'Error fetching {path}: {exc}', flush=True)
        if attempt + 1 < ATTEMPTS:
            if delay > 120:
                print('Server requested a long cooldown; deferring until the next sync.', flush=True)
                return None
            print(f'Retrying in {delay:.1f}s', flush=True)
            time.sleep(delay)
    print(f'Failed to retrieve {path} after {ATTEMPTS} attempts.', flush=True)
    return None


def save(path, obj):
    """Replace each JSON file atomically so readers never see partial JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def valid_history(matches):
    if not isinstance(matches, list) or not matches or len(matches) > MATCH_COUNT:
        return False
    ids = set()
    for match in matches:
        if not isinstance(match, dict):
            return False
        mid = match.get('match_id')
        if type(mid) is not int or mid <= 0 or mid in ids:
            return False
        ids.add(mid)
        if (type(match.get('start_time')) is not int
                or type(match.get('player_slot')) is not int
                or type(match.get('radiant_win')) is not bool
                or type(match.get('hero_id')) is not int):
            return False
    return True


def valid_detail(details, mid):
    return (isinstance(details, dict) and details.get('match_id') == mid
            and type(details.get('duration')) is int and details['duration'] > 0
            and isinstance(details.get('players'), list)
            and any(isinstance(p, dict) and p.get('account_id') == int(ACCOUNT)
                    for p in details['players']))


def parsed(details):
    return isinstance(details.get('version'), int) and details['version'] > 0


def read_detail(path, mid):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if valid_detail(value, mid) else None
    except (OSError, ValueError):
        return None


def result(match):
    return {key: match.get(key) for key in (
        'match_id', 'kills', 'deaths', 'assists', 'hero_id', 'start_time', 'lobby_type')}


def main():
    if not ACCOUNT.isdigit() or int(ACCOUNT) <= 0 or not 1 <= DETAIL_COUNT <= MATCH_COUNT <= 100:
        raise SystemExit('Expected a positive account ID and 1 <= DETAIL_COUNT <= MATCH_COUNT <= 100.')
    matches = fetch(f'/players/{ACCOUNT}/matches?' + urllib.parse.urlencode({'limit': MATCH_COUNT}))
    if not valid_history(matches):
        raise SystemExit('History unavailable, empty, or invalid; existing data was not overwritten.')
    matches = sorted(matches, key=lambda m: (m['start_time'], m['match_id']), reverse=True)
    recent = matches[:DETAIL_COUNT]
    statuses = []
    for match in recent:
        mid = match['match_id']
        target = OUT / 'matches' / f'{mid}.json'
        previous = read_detail(target, mid)
        details = fetch(f'/matches/{mid}')
        status = {'match_id': mid, 'updated': False, 'available': False,
                  'parsed': False, 'source': 'unavailable'}
        if valid_detail(details, mid):
            if previous and parsed(previous) and not parsed(details):
                details = previous
                status['source'] = 'cache'
            else:
                save(target, details)
                status.update(updated=True, source='api')
        else:
            details = previous
            status['source'] = 'cache' if previous else 'unavailable'
        if details:
            status.update(available=True, parsed=parsed(details))
        statuses.append(status)
        print(f'Match {mid}: {status}', flush=True)
        time.sleep(1.3)
    ranked = [m for m in matches if m.get('lobby_type') == 7]
    summary = {
        'account_id': int(ACCOUNT),
        'fetched_at_utc': datetime.now(timezone.utc).isoformat(),
        'match_count': len(matches),
        'ranked_match_count': len(ranked),
        'detail_count_requested': len(recent),
        'detail_count_updated': sum(s['updated'] for s in statuses),
        'detail_count_available': sum(s['available'] for s in statuses),
        'detail_count_parsed': sum(s['parsed'] for s in statuses),
        'detail_status': statuses,
        'recent_matches': [dict(result(m), won=(m['player_slot'] < 128) == m['radiant_win']) for m in recent],
        'recent_ranked': [dict(result(m), won=(m['player_slot'] < 128) == m['radiant_win']) for m in ranked[:DETAIL_COUNT]],
    }
    save(OUT / 'matches.json', matches)
    save(OUT / 'summary.json', summary)
    retained = {f'{m["match_id"]}.json' for m in recent}
    for existing in (OUT / 'matches').glob('*.json'):
        if existing.name not in retained:
            existing.unlink()
    print(f'Saved {len(matches)} matches; {summary["detail_count_available"]}/{len(recent)} details available, '
          f'{summary["detail_count_parsed"]} parsed.', flush=True)


if __name__ == '__main__':
    main()
