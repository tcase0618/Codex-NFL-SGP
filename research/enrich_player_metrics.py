"""Build a NEW GSIS-attributed player play-metric snapshot; bases stay read-only."""
import argparse
import gzip
import hashlib
import json
import shutil
import sqlite3
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from football_engine.player_metrics import CATALOG, PLAY_METRICS, play_definition
from football_engine.player_profile_store import RULES, TRACKING, aggregate, number, rule_for


def readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def context_matches(data, context):
    def n(key): return number(data.get(key))
    if context is None: return True
    if context == 'third_down': return n('down') == 3
    if context == 'red_zone': return n('yardline_100') is not None and 0 <= n('yardline_100') <= 20
    if context == 'two_minute': return n('half_seconds_remaining') is not None and 0 <= n('half_seconds_remaining') <= 120
    if context == 'fourth_close': return n('qtr') == 4 and n('score_differential') is not None and abs(n('score_differential')) <= 8
    if context == 'short_yardage': return n('ydstogo') is not None and 0 <= n('ydstogo') <= 2
    raise ValueError(context)


def contributions(data):
    """One player/play/metric contribution, using explicit GSIS IDs only."""
    def n(key): return number(data.get(key))
    if (data.get('play_type') == 'no_play' or n('special_teams_play') == 1 or
            n('two_point_attempt') == 1 or n('qb_kneel') == 1 or n('qb_spike') == 1):
        return []
    events = defaultdict(set)
    passer, rusher, receiver = (data.get(role + '_player_id') for role in ('passer', 'rusher', 'receiver'))
    if passer and n('qb_dropback') == 1:
        events[passer].update(('dropback', 'offense'))
    if rusher and n('rush_attempt') == 1:
        events[rusher].update(('rush', 'offense'))
        if n('qb_scramble') == 1:
            events[rusher].update(('scramble', 'dropback'))
        elif n('qb_scramble') == 0:
            events[rusher].add('designed_run')
    if n('pass_attempt') == 1 and n('sack') == 0:
        if passer: events[passer].add('pass')
        if receiver:
            events[receiver].add('target')
            if n('complete_pass') == 1: events[receiver].add('catch')
            if n('air_yards') is not None and n('air_yards') >= 20: events[receiver].add('deep_target')
    result = []
    for pid, eligible in events.items():
        if not pid.startswith('00-'): continue
        for mid, spec in PLAY_METRICS.items():
            if spec['event'] not in eligible or not context_matches(data, spec['context']): continue
            value = 1 if spec['operation'] == 'count' else n(spec['field'])
            if value is None: continue
            if spec['operation'] == 'ge': value = int(value >= spec['threshold'])
            if spec['operation'] == 'le': value = int(value <= spec['threshold'])
            result.append((pid, mid, value, 1))
    return result


def splits(data):
    result = ['season']
    if data.get('posteam_type') in ('home', 'away'): result.append(data['posteam_type'])
    qtr = number(data.get('qtr'))
    if qtr in (1, 2): result.append('first_half')
    if qtr in (3, 4): result.append('second_half')
    if number(data.get('down')) == 3: result.append('third_down')
    return result


def diagnosis(db):
    counts = Counter()
    fields = defaultdict(Counter)
    for season, kind, payload in db.execute('SELECT season,kind,data FROM observations WHERE game_type="REG"'):
        counts[season, kind] += 1
        if season >= 2024:
            for key, value in json.loads(payload).items():
                if number(value) is not None: fields[season, kind][key] += 1
    return {'rows': {f'{season}:{kind}': value for (season, kind), value in sorted(counts.items())},
            'observed_numeric_fields_2024_onward': {f'{season}:{kind}': dict(value) for (season, kind), value in sorted(fields.items())},
            'limits': ['2019 weekly stats absent; 2025/2026 stats are partial ESPN supplements.',
                       'No NGS passing/rushing/receiving observations in 2025 or 2026; only 8 NGS rows total in 2024.',
                       'Existing weekly field names match implemented basic-stat adapters; missing 2026 EPA/tracking is a source gap, not a safe alias.',
                       'No name joins; no pressure, routes, grades, participation or availability inferred.']}


def compare_coverage(base, derived):
    """Exact season-view availability under adapters; groups match profile roles.

    Additive observed zeros remain populated exactly as the existing API does.
    Population is union of base observations and newly attributed play rows.
    """
    roles = dict(base.execute('SELECT id,position FROM players'))
    available = defaultdict(set)
    role_rules = {}
    for role, metrics in CATALOG.items():
        role_rules[role] = {rule_for(metric) for metric in metrics if rule_for(metric)}
    for pid, season, kind, payload in base.execute('SELECT player_id,season,kind,data FROM observations WHERE game_type="REG"'):
        key = pid, season
        available[key]  # retain player-seasons even when every metric is missing
        candidates = [rule for rule in role_rules.get(roles.get(pid), ()) if TRACKING.get(rule, 'stats') == kind and rule not in available[key]]
        if not candidates: continue
        data = json.loads(payload)
        for rule in candidates:
            if aggregate([data], rule)[0] is not None: available[key].add(rule)
    play_available = defaultdict(set)
    for pid, season, payload in derived.execute('SELECT player_id,season,data FROM observations WHERE kind="pbp_season" AND game_type="REG"'):
        row = json.loads(payload)
        play_available[pid, season].update(key[:-2] for key, value in row.items() if key.endswith(':d') and number(value) is not None and number(value) > 0)
    counts = defaultdict(Counter)
    for pid, season in available.keys() | play_available.keys():
        role = roles.get(pid, 'UNKNOWN')
        bucket = counts[season, role]
        bucket['player_seasons'] += 1
        for metric in CATALOG.get(role, []):
            bucket['defined_slots'] += 1
            before = rule_for(metric) in available[pid, season]
            after = before or metric['id'] in play_available[pid, season]
            bucket['before_populated'] += before
            bucket['after_populated'] += after
            bucket['added_slots'] += after and not before
    result = []
    for (season, role), counter in sorted(counts.items()):
        row = dict(season=season, position=role, **counter)
        for prefix in ('before', 'after'):
            row[prefix + '_rate'] = round(counter[prefix + '_populated'] / counter['defined_slots'], 6) if counter['defined_slots'] else None
        result.append(row)
    return result


def run(base, team_database, output):
    base, team_database, output = map(Path, (base, team_database, output))
    if not base.is_file() or not team_database.is_file(): raise ValueError('existing input databases required')
    if output.exists(): raise FileExistsError(output)
    output.mkdir(parents=True)
    archive = output / 'raw'; archive.mkdir()
    start = datetime.now(timezone.utc).isoformat()
    before_hash = {str(path.resolve()): sha256(path) for path in (base, team_database)}
    lineage = {'created_at': start, 'bases_sha256': before_hash, 'sources': [], 'definitions': [],
               'historically_pregame_certified': False, 'identity': 'explicit stable GSIS IDs only'}
    with readonly(base) as source, readonly(team_database) as teams, sqlite3.connect(output / 'players.sqlite') as db:
        source.backup(db)
        identities = {row[0] for row in db.execute('SELECT id FROM players')}
        diagnostic = diagnosis(source)
        source_map = {}
        for sid, reference, digest, observed_at, path, kind in teams.execute('SELECT id,reference,sha256,observed_at,path,kind FROM sources WHERE kind="pbp"'):
            path = Path(path)
            if sha256(path) != digest: raise ValueError(f'raw source hash mismatch: {path}')
            target = archive / path.name
            shutil.copyfile(path, target)
            if sha256(target) != digest: raise ValueError(f'archive hash mismatch: {target}')
            source_map[sid] = db.execute('INSERT INTO sources(reference,sha256,observed_at,kind,path) VALUES(?,?,?,?,?)',
                (reference, digest, start, 'pbp_player_events_v1', str(target.resolve()))).lastrowid
            lineage['sources'].append(dict(team_source_id=sid, player_source_id=source_map[sid], reference=reference,
                sha256=digest, path=str(target.resolve()), original_observed_at=observed_at, observed_at=start,
                grain='raw public play-by-play; game_id + play_id'))
        for url in ['https://nflfastr.com/articles/field_descriptions.html', 'https://nflfastr.com/articles/stats_variables.html']:
            target = archive / ('definition-' + hashlib.sha256(url.encode()).hexdigest()[:12] + '.html')
            try:
                with urllib.request.urlopen(url, timeout=40) as response: target.write_bytes(response.read())
                lineage['definitions'].append(dict(reference=url, path=str(target.resolve()), sha256=sha256(target), status='ARCHIVED'))
            except Exception as exc:
                lineage['definitions'].append(dict(reference=url, status='UNAVAILABLE', error=str(exc)))
        # A per-source/game batch bounds memory and preserves traceable grain.
        current = None
        batch = {}
        event_counts, unknown, play_counts = Counter(), Counter(), Counter()
        evidence = gzip.open(output / 'event-trace.jsonl.gz', 'wt', encoding='utf-8')
        def flush():
            for (pid, season, week, split, sid), row in batch.items():
                db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?)',
                    (pid, season, week, 'REG', 'pbp_' + split, json.dumps(row, separators=(',', ':')), source_map[sid]))
            batch.clear()
        sql = 'SELECT p.game_id,p.play_id,p.season,p.source_id,p.data,g.week,g.game_date FROM plays p JOIN games g ON g.id=p.game_id ORDER BY p.game_id,p.play_id'
        for gid, play_id, season, sid, payload, week, game_date in teams.execute(sql):
            if current != gid:
                if current is not None:
                    flush()
                    if len(play_counts) and sum(play_counts.values()) % 10000 < 300: db.commit()
                current = gid
            data = json.loads(payload)
            play_counts[season] += 1
            values = contributions(data)
            if not values: continue
            used = []
            for pid, mid, n, d in values:
                if pid not in identities:
                    unknown[pid] += 1
                    continue
                used.append((pid, mid, n, d))
                event_counts[season, mid] += 1
                for split in splits(data):
                    key = pid, season, week, split, sid
                    row = batch.setdefault(key, {'game_id': gid, 'game_date': game_date, 'team': data.get('posteam'),
                        'home_away': data.get('posteam_type'), 'definition_revision': 'GSIS_PLAYER_EVENTS_V1'})
                    row[mid + ':n'] = row.get(mid + ':n', 0) + n
                    row[mid + ':d'] = row.get(mid + ':d', 0) + d
            if used:
                # Raw bytes also archived separately; trace keys identify exact
                # source rows and every contributed metric numerator/denominator.
                evidence.write(json.dumps(dict(game_id=gid, play_id=play_id, source_id=source_map[sid], values=used), separators=(',', ':')) + '\n')
        flush(); evidence.close(); db.commit()
        (output / 'IMPORT-COMPLETE.json').write_text(json.dumps({
            'status': 'COMMITTED', 'database': str((output / 'players.sqlite').resolve()),
            'created_at': datetime.now(timezone.utc).isoformat(),
            'play_counts': dict(play_counts), 'verification': 'coverage and final integrity checks pending'
        }, indent=2), encoding='utf-8')
        coverage = compare_coverage(source, db)
        years = sorted(play_counts)
        source_years = sorted({int(Path(s['path']).name.split('_')[-1].split('.')[0]) for s in lineage['sources']})
        report = dict(schema='PLAYER_METRIC_ENRICHMENT_V1', created_at=start,
            database=str((output / 'players.sqlite').resolve()), covered_seasons=years,
            source_years=source_years, latest_covered_season=max(years, default=None),
            base_stats_seasons=sorted({int(key.split(':')[0]) for key in diagnostic['rows'] if key.endswith(':stats')}),
            play_counts=dict(play_counts), derived_rows=db.execute('SELECT count(*) FROM observations WHERE kind LIKE "pbp_%"').fetchone()[0],
            event_counts={f'{year}:{metric}': n for (year, metric), n in sorted(event_counts.items())},
            unknown_gsis_ids=dict(unknown), coverage_by_season_position=coverage, source_diagnosis=diagnostic,
            coverage_grain='player season catalog slot, season view; same latest canonical position as PlayerProfileStore; union of base and derived player-seasons',
            source_definition_links=[row['reference'] for row in lineage['definitions']],
            limits=diagnostic['limits'] + ['Play coverage is limited to archived scheduled final games; no unavailable games or actors are zero-filled.',
                'Full play EPA on targeted plays is not isolated receiver contribution.',
                'Existing observed weekly values have precedence; no mixing of partial weekly and play denominators.',
                'No calibrated grades, availability or betting permissions supplied.'],
            historically_pregame_certified=False, status='PARTIAL_PUBLIC_EVENT_COVERAGE')
        db.execute('INSERT INTO metadata VALUES(?,?)', ('player_metric_enrichment_v1', json.dumps(report)))
        db.execute('CREATE TABLE player_event_metric_definitions(id TEXT PRIMARY KEY,data TEXT)')
        db.executemany('INSERT INTO player_event_metric_definitions VALUES(?,?)', [(mid, json.dumps(play_definition(mid))) for mid in PLAY_METRICS])
        # Match the overview contract already consumed by the terminal.
        old_summary = json.loads(db.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()[0])
        old_summary['base_snapshot_created_at'] = old_summary.get('created_at')
        old_summary['created_at'] = start
        old_summary['weekly_missing_seasons_scope'] = 'Upstream weekly stats only; separate public play-event coverage is available for these seasons.'
        old_summary['coverage_by_source'] = {'weekly_stats_missing_seasons': old_summary.get('weekly_missing_seasons', []),
            'play_events_covered_seasons': years, 'ngs_latest_observed_season': 2024,
            'current_season_play_metrics': 'PARTIAL_PUBLIC_EVENT_COVERAGE'}
        old_summary.setdefault('observations', {})['pbp_derived'] = report['derived_rows']
        old_summary.update(latest_season=max(years, default=None), covered_seasons=years, source_years=source_years, latest_covered_season=max(years, default=None),
            player_metric_enrichment=report['status'], derived_rows=report['derived_rows'], source_definition_links=report['source_definition_links'])
        db.execute("UPDATE metadata SET value=? WHERE key='summary'", (json.dumps(old_summary),))
        db.commit()
        report['integrity_check'] = db.execute('PRAGMA integrity_check').fetchone()[0]
    after_hash = {str(path.resolve()): sha256(path) for path in (base, team_database)}
    if after_hash != before_hash: raise RuntimeError('input database changed during enrichment')
    report['bases_unchanged'] = True
    for name, value in [('coverage.json', report), ('lineage.json', lineage), ('definitions.json', {mid: play_definition(mid) for mid in PLAY_METRICS})]:
        (output / name).write_text(json.dumps(value, indent=2), encoding='utf-8')
    manifest = {path.name: sha256(path) for path in output.iterdir() if path.is_file()}
    (output / 'sha256.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--team-database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = run(args.base, args.team_database, args.output)
    print(json.dumps({key: value for key, value in report.items() if key not in ('event_counts', 'source_diagnosis', 'coverage_by_season_position')}, indent=2))
