#!/usr/bin/env python3
"""Check consistency and actual player data before committing a sync."""
import json
from datetime import datetime

import fetch


def validate():
    matches = json.loads((fetch.OUT / 'matches.json').read_text())
    summary = json.loads((fetch.OUT / 'summary.json').read_text())
    assert fetch.valid_history(matches), 'Invalid match history'
    assert summary['account_id'] == int(fetch.ACCOUNT), 'Wrong account'
    assert summary['match_count'] == len(matches), 'Match count mismatch'
    assert datetime.fromisoformat(summary['fetched_at_utc']).tzinfo, 'Missing UTC timestamp'
    expected = [m['match_id'] for m in matches[:fetch.DETAIL_COUNT]]
    statuses = summary['detail_status']
    assert [s['match_id'] for s in statuses] == expected, 'Wrong detail selection'
    assert summary['detail_count_requested'] == len(expected), 'Wrong requested count'
    for status in statuses:
        path = fetch.OUT / 'matches' / f'{status["match_id"]}.json'
        if status['available']:
            detail = json.loads(path.read_text())
            assert fetch.valid_detail(detail, status['match_id']), 'Invalid match detail or wrong player'
            assert status['parsed'] == fetch.parsed(detail), 'Parsed status mismatch'
        else:
            assert not path.exists(), 'Unreported detail file'
    for field, flag in [('available', 'available'), ('updated', 'updated'), ('parsed', 'parsed')]:
        assert summary[f'detail_count_{field}'] == sum(s[flag] for s in statuses), 'Detail count mismatch'
    actual = {p.name for p in (fetch.OUT / 'matches').glob('*.json')}
    assert actual == {f'{s["match_id"]}.json' for s in statuses if s['available']}, 'Stale detail files'
    print(f'Validated {len(matches)} matches for account {fetch.ACCOUNT}; '
          f'{summary["detail_count_available"]}/{len(expected)} details, '
          f'{summary["detail_count_parsed"]} parsed.')


if __name__ == '__main__':
    validate()
