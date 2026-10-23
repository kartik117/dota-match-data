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
DETAIL_COUNT = int(os.environ.get('DETAIL_COUNT', '50'))
OUT = pathlib.Path('data')
BASE = 'https://api.opendota.com/api'
ATTEMPTS = 4
LAST_ERROR = None
API_BLOCKED = False


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


def fetch(path, method='GET'):
    global LAST_ERROR, API_BLOCKED
    LAST_ERROR = None
    if API_BLOCKED:
        LAST_ERROR = 'api_cooldown'
        return None
    # Parsing POSTs cost ten calls and must never be blindly retried.
    time.sleep(12 if method == 'POST' else 1.3)
    attempts = 1 if method == 'POST' else ATTEMPTS
    for attempt in range(attempts):
        delay = retry_delay(attempt)
        try:
            req = urllib.request.Request(BASE + path, headers={
                'User-Agent': 'dota-match-data-sync/2.0', 'Accept': 'application/json'},
                method=method, data=b'' if method == 'POST' else None)
            with urllib.request.urlopen(req, timeout=45) as response:
                value = json.load(response)
                LAST_ERROR = None
                return value
        except urllib.error.HTTPError as exc:
            LAST_ERROR = exc.code
            print(f'HTTP {exc.code} fetching {path}', flush=True)
            if exc.code in (401, 403) or (exc.code == 429 and attempt + 1 >= attempts):
                API_BLOCKED = True
            if exc.code not in (408, 429, 500, 502, 503, 504):
                exc.close()
                return None
            delay = retry_delay(attempt, exc.headers.get('Retry-After'))
            exc.close()
        except (urllib.error.URLError, TimeoutError, ValueError, OSError, http.client.HTTPException) as exc:
            LAST_ERROR = 'network_or_json_error'
            print(f'Error fetching {path}: {exc}', flush=True)
        if attempt + 1 < attempts:
            if delay > 120:
                API_BLOCKED = True
                print('Server requested a long cooldown; deferring until the next sync.', flush=True)
                return None
            print(f'Retrying in {delay:.1f}s', flush=True)
            time.sleep(delay)
    print(f'Failed to retrieve {path} after {attempts} attempts.', flush=True)
    return None


def save(path, obj, compact=False):
    """Replace each JSON file atomically so readers never see partial JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(obj, indent=None if compact else 2, separators=(',', ':') if compact else None, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def valid_history(matches, limit=None):
    if not isinstance(matches, list) or not matches or len(matches) > (limit or MATCH_COUNT):
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


def load_state():
    path = OUT / 'sync_state.json'
    if not path.exists():
        return {'matches': {}}
    try:
        state = json.loads(path.read_text())
        if isinstance(state, dict) and isinstance(state.get('matches'), dict):
            return state
    except (OSError, ValueError):
        pass
    raise SystemExit('Invalid sync_state.json; refusing to discard API cooldown and parse request history.')


def parse_eligibility(history, details, meta, now):
    import analysis
    if analysis.replay_parsed(details):
        return 'already_parsed'
    if not details or not details.get('replay_url'):
        return 'replay_url_unavailable'
    end = history['start_time'] + (details.get('duration') or 0)
    # Conservative eligibility window, not a guarantee that Valve still serves the replay.
    if not 0 <= now - end <= 7 * 86400:
        return 'outside_conservative_7_day_window'
    request = meta.get('parse_request', {})
    if request.get('attempts', 0) >= 3:
        return 'attempt_limit'
    if now < request.get('next_request_at', 0):
        return 'request_cooldown'
    return 'eligible'


def request_parse(mid, meta, now):
    previous = meta.get('parse_request', {})
    # Persist intent before POST. A dropped response must not cause repeated jobs.
    request = {'attempts': previous.get('attempts', 0) + 1,
               'requested_at': now, 'next_request_at': now + 48 * 3600,
               'status': 'submission_unknown'}
    meta['parse_request'] = request
    save(OUT / 'sync_state.json', STATE)
    response = fetch(f'/request/{mid}', method='POST')
    job = response.get('job') if isinstance(response, dict) else None
    if isinstance(job, dict) and job.get('jobId') is not None:
        request.update(status='queued', job_id=job['jobId'])
        meta['next_detail_check_at'] = now + 6 * 3600
    else:
        request.update(status='rejected_or_unknown', error=LAST_ERROR or 'no_job_returned')
    save(OUT / 'sync_state.json', STATE)
    return request


def check_parse_job(mid, meta, now):
    request = meta.get('parse_request')
    if not request or request.get('status') != 'queued' or now < request.get('next_job_check_at', 0):
        return
    job_id = str(request.get('job_id', ''))
    if not job_id.isdigit():
        return
    response = fetch(f'/request/{job_id}')
    request['next_job_check_at'] = now + 6 * 3600
    if LAST_ERROR is not None:
        request['last_job_check_error'] = LAST_ERROR
    elif response is None:
        # Null means absent from queue, not proof of successful parsing.
        request['status'] = 'finished_or_expired'
        meta['next_detail_check_at'] = now
    elif isinstance(response, dict):
        request['job_state'] = response.get('state')
        if response.get('state') in ('completed', 'failed'):
            request['status'] = response['state']
            meta['next_detail_check_at'] = now


STATE = {}


def main():
    import analysis
    global STATE
    if not ACCOUNT.isdigit() or int(ACCOUNT) <= 0 or not 1 <= DETAIL_COUNT <= 50 or not 1 <= MATCH_COUNT <= 100:
        raise SystemExit('Expected positive account, history count <=100 and ranked detail count <=50.')
    matches = fetch(f'/players/{ACCOUNT}/matches?' + urllib.parse.urlencode({'limit': MATCH_COUNT}))
    ranked = fetch(f'/players/{ACCOUNT}/matches?' + urllib.parse.urlencode({'limit': DETAIL_COUNT, 'lobby_type': 7}))
    if not valid_history(matches) or not valid_history(ranked, DETAIL_COUNT) or any(m.get('lobby_type') != 7 for m in ranked):
        raise SystemExit('History unavailable, empty, or invalid; existing data was not overwritten.')
    matches = sorted(matches, key=lambda m: (m['start_time'], m['match_id']), reverse=True)
    recent = sorted(ranked, key=lambda m: (m['start_time'], m['match_id']), reverse=True)
    STATE = load_state()
    now = time.time()
    parse_enabled = os.environ.get('REQUEST_PARSING', 'true').lower() == 'true'
    budget = max(0, min(3, int(os.environ.get('MAX_PARSE_REQUESTS', '3'))))
    force = os.environ.get('FORCE_REFRESH', 'false').lower() == 'true'
    statuses = []
    for match in recent:
        mid = match['match_id']
        target = OUT / 'matches' / f'{mid}.json'
        previous = read_detail(target, mid)
        meta = STATE['matches'].setdefault(str(mid), {})
        status = {'match_id': mid, 'updated': False, 'available': bool(previous),
                  'parsed': parsed(previous) if previous else False, 'source': 'cache' if previous else 'unavailable'}
        details = previous
        check_parse_job(mid, meta, now)
        full = analysis.completeness(previous, int(ACCOUNT))['full_replay_data']
        due = now >= meta.get('next_detail_check_at', 0)
        if force or (not full and due):
            response = fetch(f'/matches/{mid}')
            # Failed checks also get a cooldown. Preserve any earlier valid file.
            meta.update(last_check_at=now, next_detail_check_at=now + 6 * 3600)
            if valid_detail(response, mid):
                if not previous or not analysis.replay_parsed(previous) or analysis.replay_parsed(response):
                    details = response
                    save(target, details)
                    meta.update(last_download_at=now)
                    status.update(updated=True, available=True, source='api')
                old = now - match['start_time'] > 7 * 86400
                meta['next_detail_check_at'] = now + (7 * 86400 if old else 24 * 3600)
        coverage = analysis.completeness(details, int(ACCOUNT))
        status.update(parsed=coverage['replay_parsed'], coverage=coverage)
        if coverage['replay_parsed'] and meta.get('parse_request'):
            meta['parse_request']['status'] = 'parsed_data_available'
        eligibility = parse_eligibility(match, details, meta, now)
        status['parse_eligibility'] = eligibility
        if parse_enabled and eligibility == 'eligible' and budget > 0 and not API_BLOCKED:
            request_parse(mid, meta, now)
            budget -= 1
        status['parse_request'] = meta.get('parse_request')
        statuses.append(status)
        save(OUT / 'sync_state.json', STATE)
        print(f'Match {mid}: {coverage["category"]}, source={status["source"]}, parse={eligibility}', flush=True)
    summary = {
        'account_id': int(ACCOUNT), 'fetched_at_utc': datetime.now(timezone.utc).isoformat(),
        'match_count': len(matches), 'ranked_match_count': len(recent),
        'detail_selection': 'latest_ranked', 'detail_count_requested': len(recent),
        'detail_count_updated': sum(s['updated'] for s in statuses),
        'detail_count_available': sum(s['available'] for s in statuses),
        'detail_count_parsed': sum(s['parsed'] for s in statuses),
        'detail_count_full_replay': sum(s['coverage']['full_replay_data'] for s in statuses),
        'detail_status': statuses,
        'recent_matches': [dict(result(m), won=(m['player_slot'] < 128) == m['radiant_win']) for m in matches[:DETAIL_COUNT]],
        'recent_ranked': [dict(result(m), won=(m['player_slot'] < 128) == m['radiant_win']) for m in recent],
    }
    save(OUT / 'matches.json', matches)
    save(OUT / 'ranked_matches.json', recent)
    save(OUT / 'summary.json', summary)
    # Details are a permanent cache; never remove old matches.
    analysis.main()
    print(f'Saved {len(matches)} history matches and {len(recent)} ranked matches; '
          f'{summary["detail_count_available"]} details available, {summary["detail_count_full_replay"]} full replay.', flush=True)


if __name__ == '__main__':
    main()
