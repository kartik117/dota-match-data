"""Deterministic, source-backed match stories; all generation is offline."""
import hashlib
import json
from collections import Counter

import analysis

PRIORITY_MATCH = 9057336754
MAJOR_ITEMS = frozenset('''power_treads phase_boots arcane_boots tranquil_boots travel_boots travel_boots_2
 guardian_greaves boots_of_bearing blink overwhelming_blink swift_blink arcane_blink
 black_king_bar manta yasha sange_and_yasha kaya_and_sange yasha_and_kaya
 sphere lotus_orb force_staff hurricane_pike glimmer_cape aether_lens octarine_core
 aghanims_shard ultimate_scepter ultimate_scepter_2 refresher refresher_shard
 mask_of_madness echo_sabre harpoon armlet radiance battlefury maelstrom mjollnir gleipnir
 desolator lesser_crit greater_crit silver_edge invis_sword monkey_king_bar butterfly
 satanic skadi heart assault shivas_guard bloodstone eternal_shroud pipe crimson_guard
 vanguard blade_mail heavens_halberd nullifier bloodthorn orchid diffusal_blade disperser
 abyssal_blade basher rapier moon_shard ethereal_blade dagon dagon_2 dagon_3 dagon_4 dagon_5
 hand_of_midas helm_of_the_dominator helm_of_the_overlord mekansm vladmir wraith_pact
 solar_crest spirit_vessel urn_of_shadows aeon_disk wind_waker cyclones rod_of_atos
 falcon_blade dragon_lance khanda phylactery revenants_brooch meteor_hammer'''.split())
BASE_RAW = 'https://raw.githubusercontent.com/kartik117/dota-match-data/main/'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def ref(pointer, value):
    return {'pointer': pointer, 'value': value}


def clock(seconds):
    sign = '-' if seconds < 0 else ''
    seconds = int(abs(seconds))
    return f'{sign}{seconds // 60:02d}:{seconds % 60:02d}'


def load_names(out):
    path = out / 'constants' / 'heroes.json'
    return json.loads(path.read_text())['heroes'] if path.exists() else {}


def roster(detail, names, account):
    result = []
    for index, p in enumerate(detail.get('players', [])):
        hero = names.get(str(p.get('hero_id')), {})
        slot = p.get('player_slot')
        side = ('Radiant' if slot < 128 else 'Dire') if type(slot) is int else None
        result.append({'index': index, 'account_id': p.get('account_id'),
                       'hero_id': p.get('hero_id'), 'hero': hero.get('localized_name', f'Hero {p.get("hero_id")}'),
                       'unit': hero.get('name'), 'player_slot': slot, 'side': side,
                       'you': p.get('account_id') == account,
                       'source': f'/players/{index}'})
    return result


def who(person, mine):
    if person['you']:
        return f'You ({person["hero"]})'
    relation = ('Ally' if person['side'] == mine['side'] else 'Opponent') if person['side'] and mine['side'] else 'Player'
    return f'{relation} {person["hero"]} (slot {person["player_slot"]})'


def participant(p, role):
    return {'role': role, 'account_id': p['account_id'], 'hero': p['hero'],
            'hero_id': p['hero_id'], 'player_slot': p['player_slot'], 'source': p['source']}


def advantage_series(detail, mine, field):
    times = detail['players'][mine['index']].get('times')
    values = detail.get(field)
    if (not isinstance(times, list) or not isinstance(values, list) or not times
            or len(times) != len(values) or not all(analysis.number(t) for t in times)
            or any(a >= b for a, b in zip(times, times[1:]))):
        return []
    sign = 1 if mine['side'] == 'Radiant' else -1 if mine['side'] == 'Dire' else None
    if sign is None:
        return []
    return [{'time': t, 'your_team_advantage': sign * value,
             'raw_radiant_advantage': value,
             'evidence': [ref(f'/players/{mine["index"]}/times/{i}', t), ref(f'/{field}/{i}', value)]}
            for i, (t, value) in enumerate(zip(times, values)) if analysis.number(value)]


def bracket(series, start, end):
    # Explicit ±60s target window; only real samples with <=60s additional gap.
    before = [p for p in series if start - 120 <= p['time'] <= start - 60]
    after = [p for p in series if end + 60 <= p['time'] <= end + 120]
    a, b = (before[-1] if before else None), (after[0] if after else None)
    return {'before': a, 'after': b,
            'change': b['your_team_advantage'] - a['your_team_advantage'] if a and b else None}


def extract_events(detail, people, mine):
    events, missing = [], []
    units = {}
    for p in people:
        if p['unit']:
            units.setdefault(p['unit'], []).append(p)
    def unique(unit):
        candidates = units.get(unit, [])
        return candidates[0] if len(candidates) == 1 else None
    def add(kind, time, text, people_refs, evidence, **extra):
        if not analysis.number(time):
            missing.append(f'Event with unavailable timestamp: {evidence[0]["pointer"]}')
            return None
        e = dict(kind=kind, time=time, end_time=time, text=text,
                 participants=people_refs, evidence=evidence, **extra)
        events.append(e)
        return e
    deaths = {}
    for p in people:
        data = detail['players'][p['index']]
        path = p['source']
        logs = data.get('deaths_log')
        if not isinstance(logs, list):
            missing.append(path + '/deaths_log unavailable')
        else:
            for i, log in enumerate(logs):
                killer = unique(log.get('key'))
                participants = [participant(p, 'victim')]
                if killer:
                    participants.append(participant(killer, 'killer_recorded_in_death_log'))
                killer_name = who(killer, mine) if killer else f'unit {log.get("key", "unavailable")}'
                e = add('death', log.get('time'), f'{who(p, mine)} died; recorded killer: {killer_name}.',
                        participants, [ref(f'{path}/deaths_log/{i}', log)])
                if e:
                    deaths[(e['time'], log.get('key'), p['unit'])] = e
        buybacks = data.get('buyback_log')
        if not isinstance(buybacks, list):
            missing.append(path + '/buyback_log unavailable')
        else:
            for i, log in enumerate(buybacks):
                add('buyback', log.get('time'), f'{who(p, mine)} bought back.', [participant(p, 'buyer')],
                    [ref(f'{path}/buyback_log/{i}', log)])
        purchases = data.get('purchase_log')
        if not isinstance(purchases, list):
            missing.append(path + '/purchase_log unavailable')
        else:
            for i, log in enumerate(purchases):
                if log.get('key') in MAJOR_ITEMS:
                    add('major_item', log.get('time'), f'{who(p, mine)}: purchase log recorded `{log["key"]}`.',
                        [participant(p, 'purchaser')], [ref(f'{path}/purchase_log/{i}', log)])
    # Merge a kill log with its exact matching death, keeping both original records.
    for p in people:
        logs = detail['players'][p['index']].get('kills_log')
        if not isinstance(logs, list):
            missing.append(p['source'] + '/kills_log unavailable')
            continue
        for i, log in enumerate(logs):
            evidence = ref(f'{p["source"]}/kills_log/{i}', log)
            existing = deaths.get((log.get('time'), p['unit'], log.get('key')))
            if existing and p['unit'] is not None:
                existing['evidence'].append(evidence)
                continue
            victim = unique(log.get('key'))
            participants = [participant(p, 'killer_recorded_in_kill_log')]
            if victim:
                participants.append(participant(victim, 'victim'))
            target = who(victim, mine) if victim else f'unit {log.get("key", "unavailable")}'
            add('kill', log.get('time'), f'{who(p, mine)}: kill log recorded {target}.', participants, [evidence])
    objectives = detail.get('objectives')
    if not isinstance(objectives, list):
        missing.append('/objectives unavailable')
    else:
        for i, log in enumerate(objectives):
            key, kind = str(log.get('key', '')), str(log.get('type', ''))
            target = ('Radiant' if 'goodguys' in key else 'Dire' if 'badguys' in key else None)
            building = kind == 'building_kill'
            category = ('tower' if 'tower' in key else 'barracks' if 'rax' in key else 'ancient' if 'fort' in key else 'building') if building else 'roshan' if 'ROSHAN' in kind else 'aegis' if 'AEGIS' in kind else 'objective'
            actors = [p for p in people if p['player_slot'] == log.get('player_slot') and log.get('player_slot') is not None]
            actor_text = who(actors[0], mine) if len(actors) == 1 else f'unit {log.get("unit", "unavailable")}'
            if building:
                relation = ('Your team lost' if target == mine['side'] else 'Opposing team lost') if target and mine['side'] else 'Building destroyed:'
                text = f'{relation} `{key}`; recorded actor: {actor_text}.'
            else:
                text = f'Objective log recorded `{kind}`'
                if 'team' in log:
                    text += f'; original team field: {log["team"]}'
                if actors:
                    text += f'; recorded player: {actor_text}'
                text += '.'
            add(category, log.get('time'), text, [participant(p, 'recorded_objective_actor') for p in actors],
                [ref(f'/objectives/{i}', log)], target_side=target)
    fights = detail.get('teamfights')
    if not isinstance(fights, list):
        missing.append('/teamfights unavailable')
    else:
        for i, fight in enumerate(fights):
            fp = fight.get('players')
            aligned = isinstance(fp, list) and len(fp) == len(people)
            participants, evidence = [], [ref(f'/teamfights/{i}/{k}', fight[k]) for k in ('start', 'end', 'last_death', 'deaths') if k in fight]
            your_stats = None
            if aligned:
                for p, stats in zip(people, fp):
                    if not isinstance(stats, dict):
                        continue
                    # Income/XP/healing alone do not establish physical/combat participation.
                    active = any(analysis.number(stats.get(k)) and stats[k] > 0 for k in ('damage', 'deaths', 'buybacks')) or any(analysis.number(v) and v > 0 for k in ('killed', 'ability_uses', 'item_uses') for v in (stats.get(k) or {}).values())
                    if active:
                        participants.append(participant(p, 'recorded_activity_in_detected_interval'))
                    for k in ('damage', 'deaths', 'buybacks', 'killed', 'gold_delta', 'xp_delta', 'healing'):
                        if k in stats:
                            evidence.append(ref(f'/teamfights/{i}/players/{p["index"]}/{k}', stats[k]))
                    if p['you']:
                        your_stats = stats
            else:
                missing.append(f'/teamfights/{i}/players: unavailable or cannot align with roster')
            text = 'OpenDota detected a teamfight'
            end = fight.get('end')
            if analysis.number(end):
                text += f' through {clock(end)}'
            if your_stats:
                text += f'; your recorded damage: {your_stats.get("damage", "unavailable")}, deaths: {your_stats.get("deaths", "unavailable")}, gold delta: {your_stats.get("gold_delta", "unavailable")}, XP delta: {your_stats.get("xp_delta", "unavailable")}'
            else:
                text += '; your fight statistics unavailable'
            e = add('teamfight', fight.get('start'), text + '.', participants, evidence)
            if e and analysis.number(end) and end >= e['time']:
                e['end_time'] = end
    return sorted(events, key=lambda e: (e['time'], e['kind'], e['evidence'][0]['pointer'])), sorted(set(missing))


def turning_points(events, gold, xp, mine):
    findings = []
    for event in events:
        if event['kind'] not in ('tower', 'barracks', 'ancient') or event.get('target_side') != mine['side']:
            continue
        # Only samples strictly before the objective. No objective outcome leaks into the swing.
        endpoints = [p for p in gold if 0 < event['time'] - p['time'] <= 120]
        if not endpoints:
            continue
        end = endpoints[-1]
        starts = [p for p in gold if 240 <= end['time'] - p['time'] <= 300]
        if not starts:
            continue
        start = starts[0]
        delta = end['your_team_advantage'] - start['your_team_advantage']
        if delta > -2500:
            continue
        xp_start = next((p for p in xp if p['time'] == start['time']), None)
        xp_end = next((p for p in xp if p['time'] == end['time']), None)
        nearby = [e for e in events if start['time'] <= e['time'] <= event['time'] and e['kind'] in ('kill', 'death', 'buyback', 'teamfight')]
        findings.append({'start_time': start['time'], 'swing_end_time': end['time'], 'objective_time': event['time'],
                         'gold_before': start['your_team_advantage'], 'gold_after': end['your_team_advantage'],
                         'gold_change': delta, 'xp_change': xp_end['your_team_advantage'] - xp_start['your_team_advantage'] if xp_start and xp_end else None,
                         'objective': event['text'], 'participants': event['participants'],
                         'evidence': start['evidence'] + end['evidence'] + (xp_start['evidence'] + xp_end['evidence'] if xp_start and xp_end else []) + event['evidence'],
                         'nearby_event_sources': [e['evidence'][0]['pointer'] for e in nearby],
                         'interpretation': 'Potential turning point: observed adverse team gold-advantage swing followed by a building loss. Temporal association only; no causal or mistake classification.'})
    return findings


def reconstruct(history, detail, names, account=analysis.ACCOUNT):
    if not analysis.replay_parsed(detail):
        return None
    people = roster(detail, names, account)
    mine = next((p for p in people if p['you']), None)
    if mine is None:
        return None
    events, missing = extract_events(detail, people, mine)
    gold = advantage_series(detail, mine, 'radiant_gold_adv')
    xp = advantage_series(detail, mine, 'radiant_xp_adv')
    for label, values in [('radiant_gold_adv', gold), ('radiant_xp_adv', xp)]:
        if not values:
            missing.append(f'/{label}: unavailable or cannot align with player times')
    for e in events:
        e['advantage'] = {'gold': bracket(gold, e['time'], e['end_time']),
                          'xp': bracket(xp, e['time'], e['end_time'])}
    findings = turning_points(events, gold, xp, mine)
    p = detail['players'][mine['index']]
    slot, winner = p.get('player_slot'), detail.get('radiant_win')
    outcome = ('win' if (slot < 128) == winner else 'loss') if type(slot) is int and type(winner) is bool else 'unavailable'
    overview_evidence = [ref(f'/players/{mine["index"]}/{k}', p[k]) for k in ('account_id', 'hero_id', 'player_slot', 'kills', 'deaths', 'assists') if k in p]
    overview_evidence += [ref('/' + k, detail[k]) for k in ('duration', 'radiant_win', 'match_id') if k in detail]
    return {'match_id': history['match_id'], 'account_id': account, 'hero': mine['hero'],
            'hero_id': mine['hero_id'], 'side': mine['side'], 'outcome': outcome,
            'duration': detail.get('duration'), 'kda': {k: p.get(k) for k in ('kills', 'deaths', 'assists')},
            'overview_evidence': overview_evidence, 'coverage': analysis.completeness(detail, account),
            'roster': people, 'gold_samples': gold, 'xp_samples': xp, 'events': events, 'turning_points': findings, 'missing_information': sorted(set(missing))}


def render(story):
    from report import pretty
    mid = story['match_id']
    lines = [f'# Match {mid}: {story["hero"]} — {story["outcome"]}', '',
             f'Perspective: account **{story["account_id"]}**, {story["side"]}. '
             f'Duration: {clock(story["duration"]) if analysis.number(story["duration"]) else "unavailable"}. '
             f'K/D/A: {story["kda"]["kills"]}/{story["kda"]["deaths"]}/{story["kda"]["assists"]}.', '',
             f'[Original cached OpenDota JSON](../matches/{mid}.json). Evidence uses JSON pointers into this file; hero names use [cached constants](../constants/heroes.json).', '',
             '## Evidence and limits', '',
             'This is a chronological reconstruction of recorded events, not a replay video review. '
             'No intent, vision, map awareness, decision reason, or mistake is inferred. '
             'Missing timestamps are omitted and reported; empty logs mean no recorded entries, not proof no event occurred. '
             'Kill and death logs are merged only for exact timestamp/killer/victim matches; unmatched records remain separate observations. '
             'Purchase-log entries are not proof of delivery, use, or assembled-item completion. '
             'Detected teamfights are OpenDota intervals; listed activity does not establish exact positioning or a teamfight winner.', '',
             'Team advantage is from **your side**: positive favors your team; Dire reverses the original Radiant sign. '
             'Gold uses `radiant_gold_adv` and XP uses `radiant_xp_adv`, aligned only when arrays match recorded player `times`. '
             'No team net-worth series is fabricated from these fields. '
             'Event context targets 60 seconds before the start and 60 seconds after the end, allowing one extra minute to find a real sample. '
             'Every displayed sample keeps its original time, Radiant value, and source pointers; missing samples are unavailable, with no interpolation.', '',
             'Overview source: `' + encoded(story['overview_evidence']) + '`', '', '## Roster', '']
    for p in story['roster']:
        lines.append(f'- {p["hero"]}: slot {p["player_slot"]}, {p["side"]}, account {p["account_id"] if p["account_id"] is not None else "unavailable"}' + (' (you)' if p['you'] else '') + f'; source `{p["source"]}`.')
    lines += ['', '## Potential turning points', '',
              'Rule: your team loses at least 2,500 in recorded gold advantage over a 4–5 minute observed window, '
              'then loses a tower, barracks, or Ancient within 120 seconds of the ending sample. '
              'Only pre-objective samples qualify. This is a transparent candidate-selection heuristic; overlapping findings can describe the same broader swing.']
    if not story['turning_points']:
        lines += ['', 'No candidate met this rule using the available samples. This does not establish that the match had no turning points.']
    for f in story['turning_points']:
        lines += ['', f'### {clock(f["start_time"])}–{clock(f["objective_time"])}: potential adverse swing', '',
                  f'Your team gold advantage changed from {f["gold_before"]:+} to {f["gold_after"]:+} '
                  f'({f["gold_change"]:+}) by {clock(f["swing_end_time"])}. '
                  f'XP advantage change across the same samples: {f["xp_change"] if f["xp_change"] is not None else "unavailable"}. '
                  f'At {clock(f["objective_time"])}: {f["objective"]} '
                  'The sequence is observed; causation and responsibility are undetermined.', '',
                  '```json', pretty(f), '```']
    lines += ['', '## Recorded team-advantage samples', '',
              'Exact original fields and derived side-adjusted values. Event links below refer to these samples; no values are interpolated.', '',
              '| Time and original time pointer | Original Radiant gold (pointer) | Your gold advantage | Original Radiant XP (pointer) | Your XP advantage |',
              '| --- | --- | --- | --- | --- |']
    samples = {}
    for label in ('gold', 'xp'):
        for point in story[label + '_samples']:
            samples.setdefault(point['time'], {})[label] = point
    for time, points in sorted(samples.items()):
        point = points.get('gold') or points['xp']
        def original(label):
            p = points.get(label)
            return f'{p["raw_radiant_advantage"]:+} (`{p["evidence"][1]["pointer"]}`)' if p else 'unavailable'
        def adjusted(label):
            p = points.get(label)
            return f'{p["your_team_advantage"]:+}' if p else 'unavailable'
        lines.append(f'| <a id="sample-{int(time)}"></a>{clock(time)} (`{point["evidence"][0]["pointer"]}`) | '
                     f'{original("gold")} | {adjusted("gold")} | {original("xp")} | {adjusted("xp")} |')
    if not samples:
        lines.append('No aligned advantage samples available.')
    lines += ['', '## Chronological story', '']
    if not story['events']:
        lines.append('No timestamped events available.')
    for e in story['events']:
        lines += [f'### {clock(e["time"])} — {e["text"]}', '']
        people = [f'{p["hero"]} (account {p["account_id"] if p["account_id"] is not None else "unavailable"}; {p["role"]}; source `{p["source"]}`)' 
                  for p in e['participants']]
        lines += ['Recorded players: ' + ('; '.join(people) if people else 'unavailable; see original actor/unit fields') + '.', '']
        for label, context in e['advantage'].items():
            def sample(p):
                return f'[{clock(p["time"])}](#sample-{int(p["time"])}): {p["your_team_advantage"]:+}' if p else 'unavailable'
            lines.append(f'- Your team {label} advantage: before {sample(context["before"])}; after {sample(context["after"])}; '
                         f'change {context["change"] if context["change"] is not None else "unavailable"}.')
        lines += ['', '<details>', '<summary>Original event fields (JSON pointers and values)</summary>', '',
                  '```json', pretty(e['evidence']), '```', '', '</details>', '']
    lines += ['## Missing information', '']
    lines += ['- ' + value for value in story['missing_information']] or ['No source log/timeline gaps detected by the structural checks.']
    lines += ['', 'Core replay coverage: `' + encoded(story['coverage']) + '`. '
              'Visibility, intentions, exact positions between samples, and reasons for decisions remain unavailable.', '']
    return '\n'.join(lines)


def artifacts(histories, details, names, account, generated_at):
    documents, entries, unavailable = {}, [], []
    ordered = sorted(histories, key=lambda h: (h['match_id'] != PRIORITY_MATCH, -h['start_time']))
    for h in ordered:
        detail = details.get(h['match_id'])
        story = reconstruct(h, detail, names, account)
        if story is None:
            unavailable.append({'match_id': h['match_id'], 'reason': 'parsed replay data or account player unavailable'})
            continue
        filename = f'{h["match_id"]}.md'
        document = render(story)
        documents[filename] = document
        entries.append({'match_id': h['match_id'], 'hero': story['hero'], 'outcome': story['outcome'],
                        'account_id': account, 'path': f'data/stories/{filename}',
                        'raw_url': BASE_RAW + f'data/stories/{filename}',
                        'bytes': len(document.encode()), 'sha256': hashlib.sha256(document.encode()).hexdigest(),
                        'event_count': len(story['events']), 'event_counts': dict(Counter(e['kind'] for e in story['events'])),
                        'potential_turning_point_count': len(story['turning_points']),
                        'missing_information': story['missing_information'], 'coverage': story['coverage']})
    index = {'schema_version': 1, 'account_id': account, 'generated_at_utc': generated_at,
             'priority_match_id': PRIORITY_MATCH, 'ranked_matches_considered': len(histories),
             'story_count': len(entries), 'stories': entries, 'unavailable_matches': unavailable,
             'methodology': 'Offline deterministic reconstruction from current latest-ranked cached files. Potential turning points are observed temporal patterns, not causal or mistake judgments. Archived stories are retained; this index lists only current-window stories.'}
    return documents, index


def write(histories, details, out, account, generated_at):
    import fetch
    documents, index = artifacts(histories, details, load_names(out), account, generated_at)
    folder = out / 'stories'
    folder.mkdir(parents=True, exist_ok=True)
    for filename, document in documents.items():
        target = folder / filename
        temporary = target.with_suffix('.md.tmp')
        temporary.write_text(document, encoding='utf-8')
        temporary.replace(target)
    fetch.save(out / 'analysis' / 'stories_index.json', index)
    print(f'Generated {len(documents)} cached replay stories; {len(index["unavailable_matches"])} unparsed/unavailable.', flush=True)
