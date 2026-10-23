#!/usr/bin/env python3
"""Validate source data, selection, coverage, and every generated analysis statistic."""
import json
from datetime import datetime

import analysis
import fetch
import report
import stories


def validate():
    matches = json.loads((fetch.OUT / 'matches.json').read_text())
    ranked = json.loads((fetch.OUT / 'ranked_matches.json').read_text())
    summary = json.loads((fetch.OUT / 'summary.json').read_text())
    assert fetch.valid_history(matches), 'Invalid match history'
    assert fetch.valid_history(ranked, fetch.DETAIL_COUNT), 'Invalid ranked history'
    assert all(m.get('lobby_type') == 7 for m in ranked), 'Non-ranked selection'
    assert ranked == sorted(ranked, key=lambda m: (m['start_time'], m['match_id']), reverse=True), 'Unsorted selection'
    assert summary['account_id'] == int(fetch.ACCOUNT), 'Wrong account'
    assert summary['match_count'] == len(matches), 'Match count mismatch'
    assert datetime.fromisoformat(summary['fetched_at_utc']).tzinfo, 'Missing UTC timestamp'
    expected = [m['match_id'] for m in ranked]
    statuses = summary['detail_status']
    assert [s['match_id'] for s in statuses] == expected, 'Wrong detail selection'
    assert summary['detail_count_requested'] == len(expected), 'Wrong requested count'
    details = {}
    for status in statuses:
        mid = status['match_id']
        path = fetch.OUT / 'matches' / f'{mid}.json'
        if status['available']:
            detail = json.loads(path.read_text())
            assert fetch.valid_detail(detail, mid), 'Invalid match detail or wrong player'
            assert status['parsed'] == analysis.replay_parsed(detail), 'Parsed status mismatch'
            assert status['coverage'] == analysis.completeness(detail, int(fetch.ACCOUNT)), 'Wrong coverage'
            details[mid] = detail
        else:
            assert not path.exists(), 'Unreported detail file'
    for field in ('available', 'updated', 'parsed'):
        assert summary[f'detail_count_{field}'] == sum(s[field] for s in statuses), 'Detail count mismatch'
    last50 = json.loads((fetch.OUT / 'analysis' / 'last50.json').read_text())
    names = stories.load_names(fetch.OUT)
    report_file = fetch.OUT / 'analysis' / 'parsed11_report.json'
    assert report_file.stat().st_size < report.MAX_BYTES, 'Parsed report exceeds 200 KB'
    assert json.loads(report_file.read_text()) == report.build(last50, names), 'Parsed report differs from source'
    documents, index = stories.artifacts(ranked, details, names, int(fetch.ACCOUNT), last50['generated_at_utc'])
    assert json.loads((fetch.OUT / 'analysis' / 'stories_index.json').read_text()) == index, 'Story index differs from source'
    for filename, document in documents.items():
        assert (fetch.OUT / 'stories' / filename).read_text() == document, 'Story differs from source: ' + filename
    actual = dict(last50)
    rebuilt = analysis.build(ranked, details, int(fetch.ACCOUNT))
    # Generation times may differ; all actual statistics must match source data exactly.
    assert datetime.fromisoformat(actual.pop('generated_at_utc')).tzinfo
    rebuilt.pop('generated_at_utc')
    assert actual == rebuilt, 'Analysis differs from cached source data'
    assert summary['detail_count_full_replay'] == actual['coverage']['full_replay']
    print(f'Validated {len(matches)} history matches and {len(ranked)} ranked matches. Coverage: {actual["coverage"]}')


if __name__ == '__main__':
    validate()
