"""Small, readable parsed-match report derived from the existing analysis, offline."""
import json
import analysis
from stories import MAJOR_ITEMS

MAX_BYTES = 200_000
FIELDS = ('won', 'duration_seconds', 'kills', 'deaths', 'assists', 'kda_ratio', 'gpm', 'xpm',
          'lane_efficiency_pct', 'hero_damage', 'tower_damage', 'teamfight_participation',
          'last_hits_at_10m', 'last_hits_at_20m', 'last_hits_at_30m',
          'net_worth_at_10m', 'net_worth_at_20m', 'net_worth_at_30m',
          'death_events', 'buyback_events', 'objective_events', 'roshan_events')


def pretty(value, depth=0):
    """Indent containers; keep small scalar records on a single readable line."""
    short = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if not isinstance(value, (dict, list)) or not value:
        return short
    children = value.values() if isinstance(value, dict) else value
    if all(not isinstance(v, (dict, list)) for v in children) and len(short) <= 150:
        return short
    pad, child_pad = '  ' * depth, '  ' * (depth + 1)
    if isinstance(value, dict):
        rows = [child_pad + json.dumps(k) + ': ' + pretty(v, depth + 1) for k, v in value.items()]
        return '{\n' + ',\n'.join(rows) + '\n' + pad + '}'
    return '[\n' + ',\n'.join(child_pad + pretty(v, depth + 1) for v in value) + '\n' + pad + ']'


def project(log, fields):
    return [{k: e.get(k) for k in fields} for e in log] if isinstance(log, list) else None


def record(row, account, names):
    values = {key: row['metrics'].get(key, {}).get('value') for key in FIELDS}
    events = {key: item['value'] for key, item in row['events'].items()}
    purchases = events.get('purchase_log')
    items = None
    if isinstance(purchases, list):
        items = {}
        for item in purchases:
            key, t = item.get('key'), item.get('time')
            if key in MAJOR_ITEMS and analysis.number(t):
                items[key] = min(t, items.get(key, t))
    fights = events.get('teamfights')
    compact_fights = None
    if isinstance(fights, list):
        compact_fights = []
        for f in fights:
            stats = f.get('player') or {}
            compact_fights.append({'start': f.get('start'), 'end': f.get('end'),
                                   **{k: stats.get(k) for k in ('damage', 'deaths', 'buybacks', 'gold_delta', 'xp_delta')}})
    hero = names.get(str(row['hero_id']), {}).get('localized_name')
    missing = [k for k, v in values.items() if v is None]
    extra = {'key_item_purchase_seconds': items,
             'deaths_log': project(events.get('deaths_log'), ('time', 'key', 'time_dead', 'gold_lost')),
             'buybacks': project(events.get('buyback_log'), ('time',)),
             'teamfights': compact_fights,
             'objectives': project(events.get('objectives'), ('time', 'type', 'key', 'player_slot', 'team'))}
    missing += [k for k, v in extra.items() if v is None]
    if hero is None:
        missing.append('hero_name')
    return {'account_id': account, 'match_id': row['match_id'], 'hero_id': row['hero_id'],
            'hero_name': hero, 'outcome': 'win' if values['won'] == 1 else 'loss' if values['won'] == 0 else None,
            'coverage': row['coverage']['category'], 'stats': values, **extra,
            'missing_fields': missing,
            'source_json': f'data/matches/{row["match_id"]}.json'}


def aggregate(records):
    metrics = {}
    for key in FIELDS:
        values = [r['stats'][key] for r in records if analysis.number(r['stats'][key])]
        metrics[key] = {'mean': round(sum(values) / len(values), 4) if values else None,
                        'n': len(values), 'missing_n': len(records) - len(values)}
    items = sorted({k for r in records for k in (r['key_item_purchase_seconds'] or {})})
    timings = {}
    for item in items:
        values = [r['key_item_purchase_seconds'][item] for r in records if item in (r['key_item_purchase_seconds'] or {})]
        timings[item] = {'mean_seconds': round(sum(values) / len(values), 2), 'n': len(values),
                         'unobserved_n': len(records) - len(values)}
    fields = {key: {'n': sum(r[key] is not None for r in records),
                    'missing_n': sum(r[key] is None for r in records)}
              for key in ('key_item_purchase_seconds', 'deaths_log', 'buybacks', 'teamfights', 'objectives')}
    return {'matches': len(records), 'metrics': metrics, 'key_item_timings': timings, 'field_samples': fields}


def build(last50, names):
    records = [record(r, last50['account_id'], names) for r in last50['matches'] if r['coverage']['replay_parsed']]
    heroes = sorted({r['hero_id'] for r in records if r['hero_id'] is not None})
    return {'schema_version': 1, 'account_id': last50['account_id'],
            'generated_at_utc': last50['generated_at_utc'],
            'selection': 'All currently replay-parsed matches in the latest 50 ranked; filename retained as coverage grows.',
            'parsed_match_count': len(records), 'ranked_window_count': len(last50['matches']),
            'methodology': {'missing': 'null and missing_fields; observed empty logs are []; n counts matches with observed values.',
                            'times': 'Seconds relative to match start. Exact 10/20/30-minute samples only. No interpolation or gold-for-net-worth substitution.',
                            'kda_ratio': '(kills+assists)/deaths; null for zero deaths, while raw K/D/A remains available.',
                            'items': 'First observed purchase time for an explicit major-item allowlist in scripts/stories.py. No observed purchase does not mean time zero or prove an item was never owned.',
                            'events': 'Player deaths/buybacks and player stats within OpenDota teamfights; objectives are match-wide, not automatic player credit. Projected fields only; full source logs remain in source_json.',
                            'interpretation': 'Descriptive statistics, not causal or mistake classifications. Unknown intent, vision, and reasons for decisions are not reconstructed.'},
            'aggregate': aggregate(records),
            'by_outcome': {outcome: aggregate([r for r in records if r['outcome'] == outcome]) for outcome in ('win', 'loss')},
            'by_hero': {str(hero): {'all': aggregate([r for r in records if r['hero_id'] == hero]),
                                   'by_outcome': {outcome: aggregate([r for r in records if r['hero_id'] == hero and r['outcome'] == outcome])
                                                  for outcome in ('win', 'loss')}} for hero in heroes},
            'matches': records}


def write(last50, out, names):
    output = build(last50, names)
    text = pretty(output) + '\n'
    size = len(text.encode('utf-8'))
    if size >= MAX_BYTES:
        raise ValueError(f'Parsed report is {size} bytes, must stay below {MAX_BYTES}; no records were truncated.')
    target = out / 'analysis' / 'parsed11_report.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.json.tmp')
    temporary.write_text(text, encoding='utf-8')
    temporary.replace(target)
    print(f'Generated parsed-match report: {len(output["matches"])} matches, {size} bytes.', flush=True)
