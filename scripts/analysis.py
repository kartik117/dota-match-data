"""Evidence-based compact match analysis. Missing values are null, never inferred."""
import json
import math
from datetime import datetime, timezone

ACCOUNT = 467233306
# All fields required for our explicit 'full replay' core coverage definition.
SERIES = ('gold_t', 'xp_t', 'lh_t', 'dn_t', 'networth_t')
LOGS = ('purchase_log', 'kills_log', 'deaths_log', 'buyback_log')
SCALARS = {
    'kills': 'kills', 'deaths': 'deaths', 'assists': 'assists',
    'gpm': 'gold_per_min', 'xpm': 'xp_per_min', 'last_hits': 'last_hits',
    'denies': 'denies', 'net_worth': 'net_worth',
    'lane_efficiency_pct': 'lane_efficiency_pct',
    'hero_damage': 'hero_damage', 'tower_damage': 'tower_damage',
    'teamfight_participation': 'teamfight_participation',
    'lane_kills': 'lane_kills', 'neutral_kills': 'neutral_kills',
    'ancient_kills': 'ancient_kills', 'camps_stacked': 'camps_stacked',
    'creeps_stacked': 'creeps_stacked', 'roshan_kills': 'roshan_kills',
    'buyback_count': 'buyback_count', 'lane': 'lane', 'lane_role': 'lane_role',
    'position_est': 'position_est',
}
REPLAY_SCALARS = {'lane_efficiency_pct', 'teamfight_participation', 'lane_kills',
                  'neutral_kills', 'ancient_kills', 'camps_stacked', 'creeps_stacked',
                  'roshan_kills', 'buyback_count', 'lane', 'lane_role', 'position_est'}


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def player(detail, account=ACCOUNT):
    return next((p for p in (detail or {}).get('players', [])
                 if p.get('account_id') == account), {})


def replay_parsed(detail):
    return bool(detail and ((detail.get('od_data') or {}).get('has_parsed') is True
                           or number(detail.get('version')) and detail['version'] > 0))


def completeness(detail, account=ACCOUNT):
    p = player(detail, account)
    times = p.get('times')
    aligned = isinstance(times, list) and bool(times) and all(number(t) for t in times)
    missing = []
    if not aligned:
        missing.append('times')
    for key in SERIES:
        values = p.get(key)
        if not (aligned and isinstance(values, list) and len(values) == len(times)
                and all(number(v) for v in values)):
            missing.append(key)
    for key in LOGS:
        if not isinstance(p.get(key), list):
            missing.append(key)
    for key in ('lane_pos',):
        if not isinstance(p.get(key), dict) or not p[key]:
            missing.append(key)
    for key in ('lane_efficiency_pct', 'teamfight_participation'):
        if not number(p.get(key)):
            missing.append(key)
    for key in ('objectives', 'teamfights'):
        if not isinstance((detail or {}).get(key), list):
            missing.append(key)
    parsed = replay_parsed(detail)
    if not parsed:
        missing.append('replay_parsed')
    return {'replay_parsed': parsed, 'full_replay_data': parsed and not missing,
            'missing_core_fields': missing,
            'category': 'full_replay' if parsed and not missing else
                        'partial_replay' if parsed else 'basic_only' if detail else 'history_only'}


def observed(value, units='match'):
    return {'value': value, 'n': int(value is not None), 'unit': units}


def extract(history, detail, account=ACCOUNT):
    p = player(detail, account)
    parsed = replay_parsed(detail)
    source = dict(history, **p)
    # Match-level fields are taken from the detail response when present.
    d = dict(history, **(detail or {}))
    values = {}
    for label, key in SCALARS.items():
        value = source.get(key)
        values[label] = value if number(value) and (parsed or label not in REPLAY_SCALARS) else None
    for label, key in [('duration_seconds', 'duration'), ('hero_id', 'hero_id')]:
        value = d.get(key) if label == 'duration_seconds' else source.get(key)
        values[label] = value if number(value) else None
    slot = source.get('player_slot')
    winner = d.get('radiant_win')
    values['won'] = int((slot < 128) == winner) if number(slot) and type(winner) is bool else None
    kills, deaths, assists = (values[k] for k in ('kills', 'deaths', 'assists'))
    values['kda_ratio'] = ((kills + assists) / deaths
                           if all(number(v) for v in (kills, deaths, assists)) and deaths > 0 else None)
    times = p.get('times') if parsed and isinstance(p.get('times'), list) else None
    progression = {}
    for key in SERIES:
        series = p.get(key)
        progression[key] = ([[t, v] for t, v in zip(times, series)] if times and isinstance(series, list)
                            and len(series) == len(times) and all(number(v) for v in times + series) else None)
    snapshots = {}
    # Exact observations only: never substitute gold_t for net worth or interpolate.
    for minute in (10, 20, 30):
        fields = {}
        for label, key in [('net_worth', 'networth_t'), ('total_gold', 'gold_t'),
                           ('total_xp', 'xp_t'), ('last_hits', 'lh_t'), ('denies', 'dn_t')]:
            points = dict(progression[key] or [])
            value = points.get(minute * 60) if (values['duration_seconds'] or 0) >= minute * 60 else None
            fields[label] = observed(value)
            values[f'{label}_at_{minute}m'] = value
        snapshots[str(minute)] = fields
    events = {}
    for key in LOGS:
        events[key] = p.get(key) if parsed and isinstance(p.get(key), list) else None
    objectives = d.get('objectives') if parsed and isinstance(d.get('objectives'), list) else None
    events['objectives'] = objectives
    events['roshan_events'] = ([e for e in objectives if 'ROSHAN' in str(e.get('type', '')).upper()
                               or 'AEGIS' in str(e.get('type', '')).upper()] if objectives is not None else None)
    # teamfights.players follows match.players ordering, not player_slot indexing.
    fights = d.get('teamfights') if parsed else None
    index = next((i for i, q in enumerate(d.get('players', [])) if q.get('account_id') == account), None)
    events['teamfights'] = ([{'start': f.get('start'), 'end': f.get('end'),
                            'player': f['players'][index] if index is not None
                            and isinstance(f.get('players'), list) and index < len(f['players']) else None}
                           for f in fights] if isinstance(fights, list) else None)
    for label, key in [('kill_events', 'kills_log'), ('death_events', 'deaths_log'),
                       ('buyback_events', 'buyback_log'), ('objective_events', 'objectives'),
                       ('roshan_events', 'roshan_events')]:
        values[label] = len(events[key]) if events[key] is not None else None
    positioning = {key: observed(p.get(key) if parsed and isinstance(p.get(key), dict) else None)
                   for key in ('lane_pos', 'obs', 'sen')}
    if events['purchase_log'] is not None:
        for event in events['purchase_log']:
            key, timestamp = event.get('key'), event.get('time')
            if isinstance(key, str) and number(timestamp):
                metric = f'first_purchase_seconds:{key}'
                values[metric] = min(timestamp, values.get(metric, timestamp))
    metrics = {key: observed(value) for key, value in values.items()}
    return {'match_id': history['match_id'], 'start_time': history['start_time'],
            'hero_id': values['hero_id'], 'coverage': completeness(detail, account),
            'metrics': metrics, 'snapshots_minutes': snapshots,
            'progression': {key: observed(value) for key, value in progression.items()},
            'events': {key: observed(value) for key, value in events.items()},
            'positioning': positioning,
            'unavailable_metrics': [key for key, value in values.items() if value is None]}


def stats(values):
    good = [v for v in values if number(v)]
    return {'n': len(good), 'mean': round(sum(good) / len(good), 4) if good else None}


def aggregate(rows, keys=None):
    keys = keys if keys is not None else sorted({k for r in rows for k in r['metrics']})
    # Hero/lane IDs are categories; averaging them is not meaningful.
    keys = [k for k in keys if k not in ('hero_id', 'lane', 'lane_role', 'position_est')]
    return {'matches': len(rows), 'metrics': {k: stats([r['metrics'].get(k, {}).get('value') for r in rows]) for k in keys}}


def comparisons(rows):
    keys = sorted({k for r in rows for k in r['metrics']})
    wins = aggregate([r for r in rows if r['metrics']['won']['value'] == 1], keys)
    losses = aggregate([r for r in rows if r['metrics']['won']['value'] == 0], keys)
    delta = {}
    for key in wins['metrics']:
        a, b = wins['metrics'][key], losses['metrics'][key]
        delta[key] = {'wins_n': a['n'], 'losses_n': b['n'],
                      'win_minus_loss_mean': round(a['mean'] - b['mean'], 4) if a['n'] and b['n'] else None}
    return {'wins': wins, 'losses': losses, 'differences': delta}


def build(histories, details, account=ACCOUNT):
    rows = [extract(h, details.get(h['match_id']), account) for h in histories]
    # Every match explicitly reports missingness, including items purchased only in other games.
    metric_keys = sorted({key for row in rows for key in row['metrics']})
    for row in rows:
        for key in metric_keys:
            if key not in row['metrics']:
                row['metrics'][key] = observed(None)
                row['unavailable_metrics'].append(key)
    coverage = {category: sum(r['coverage']['category'] == category for r in rows)
                for category in ('full_replay', 'partial_replay', 'basic_only', 'history_only')}
    coverage['replay_parsed'] = sum(r['coverage']['replay_parsed'] for r in rows)
    # Explicit field coverage counts also accompany non-scalar/event data.
    fields = {}
    for group in ('progression', 'events', 'positioning'):
        for row in rows:
            for key in row[group]:
                fields[f'{group}.{key}'] = sum(r[group][key]['n'] for r in rows)
    heroes = sorted({r['hero_id'] for r in rows if r['hero_id'] is not None})
    return {'schema_version': 1, 'account_id': account,
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'selection': {'lobby_type': 7, 'requested': 50, 'available': len(rows)},
            'coverage': coverage, 'field_sample_counts': fields,
            'methodology': {
                'missing': 'null with n=0; observed zero or empty event log has n=1',
                'samples': 'n counts matches with an observed value, not events or timeline points',
                'full_replay': 'Parsed flag plus aligned gold/xp/lh/denies/net-worth timelines, purchase/kill/death/buyback logs, lane positions, lane efficiency, participation, objectives and teamfights. Does not imply every possible field exists.',
                'timelines': 'Pairs of [seconds,value]. gold_t is cumulative gold, never a net-worth substitute. Exact 10/20/30-minute samples only; shorter matches excluded.',
                'kda': '(kills+assists)/deaths; ratio unavailable when deaths=0, raw K/D/A retained',
                'purchase_samples': 'First-purchase timing means include only games with an observed purchase; no purchase is not a zero timing.',
                'events': 'Objectives and Roshan/Aegis are match-wide events, not automatically player credit. Item log times describe purchases, not guaranteed item completion.',
                'positioning': 'OpenDota lane_pos and ward heatmaps only; not a full movement trace.',
                'interpretation': 'Descriptive correlations only. Win/loss differences do not prove mistakes or causal effects; hero, role, patch, duration and selection bias are not controlled.',
                'units': 'Duration and event times in seconds; participation fraction; lane_efficiency_pct percent; other scalar units follow OpenDota field names.',
            },
            'aggregate': aggregate(rows), 'win_loss': comparisons(rows),
            'by_hero': {str(hero): {'aggregate': aggregate([r for r in rows if r['hero_id'] == hero]),
                                   'win_loss': comparisons([r for r in rows if r['hero_id'] == hero])} for hero in heroes},
            'matches': rows}


def main():
    import fetch
    histories = json.loads((fetch.OUT / 'ranked_matches.json').read_text())
    details = {h['match_id']: fetch.read_detail(fetch.OUT / 'matches' / f'{h["match_id"]}.json', h['match_id'])
               for h in histories}
    output = build(histories, details, int(fetch.ACCOUNT))
    fetch.save(fetch.OUT / 'analysis' / 'last50.json', output, compact=True)
    import report
    import stories
    report.write(output, fetch.OUT, stories.load_names(fetch.OUT))
    stories.write(histories, details, fetch.OUT, int(fetch.ACCOUNT), output['generated_at_utc'])
    print('Analysis coverage:', output['coverage'], flush=True)


if __name__ == '__main__':
    main()
