"""Build a NEW independent charting overlay from archived nflverse releases.

No network, runtime, base-store or configuration mutation is performed by build().
Archive acquisition is separately requested through collect(); its byte budget is
checked against both release metadata and received bytes.
"""
import argparse
import csv
import gzip
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from football_engine.charting_metrics import ChartingMetrics, RULES, eligible


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def rows(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8-sig', newline='') as handle:
        yield from csv.DictReader(handle)


def canonical_id(raw):
    value = str(raw)
    if value.endswith('.0'):
        value = value[:-2]
    if not value.isdigit():
        raise ValueError('invalid play ID')
    return value


def collect(output, byte_limit=250_000_000):
    """At most 13 discovery requests; only CSV assets relevant to 2024-2026.

    The aggregate NGS CSV files contain multiple seasons; retain raw bytes, but
    filter actual row seasons in build(). No login or alternate endpoint bypass.
    """
    import requests
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'raw').mkdir()
    discovery, assets = [], []
    budget = 0
    def fetch(name, url):
        nonlocal budget
        response = requests.get(url, timeout=90, stream=True)
        response.raise_for_status()
        content = bytearray()
        for chunk in response.iter_content(1024 * 1024):
            budget += len(chunk)
            if budget > byte_limit:
                raise ValueError('download byte budget exceeded')
            content.extend(chunk)
        (output / 'raw' / name).write_bytes(content)
        return {'name': name, 'url': url, 'bytes': len(content),
                'sha256': hashlib.sha256(content).hexdigest(),
                'observed_at': datetime.now(timezone.utc).isoformat()}
    for tag in ('ftn_charting', 'pbp_participation', 'snap_counts', 'nextgen_stats', 'players'):
        name = f'release-{tag}.json'
        discovery.append(fetch(name, f'https://api.github.com/repos/nflverse/nflverse-data/releases/tags/{tag}'))
        release = json.loads((output / 'raw' / name).read_bytes())
        for asset in release['assets']:
            n = asset['name']
            relevant = any(str(y) in n for y in (2024, 2025, 2026))
            wanted = (tag in ('ftn_charting', 'pbp_participation') and relevant and n.endswith('.csv'))
            wanted |= tag == 'snap_counts' and relevant and n.endswith('.csv.gz')
            wanted |= tag == 'nextgen_stats' and n in ('ngs_passing.csv.gz', 'ngs_receiving.csv.gz', 'ngs_rushing.csv.gz')
            wanted |= tag == 'players' and n == 'players.csv'
            if wanted:
                if budget + asset['size'] > byte_limit:
                    raise ValueError('declared download byte budget exceeded')
                item = fetch(n, asset['browser_download_url'])
                item.update(kind=tag, provider_updated_at=asset['updated_at'], historically_pregame_certified=False)
                assets.append(item)
    for kind in ('ftn_charting', 'participation', 'snap_counts', 'nextgen_stats'):
        for prefix, endpoint in (('doc', 'reference/load_'), ('dictionary', 'articles/dictionary_')):
            discovery.append(fetch(f'{prefix}-{kind}.html', f'https://nflreadr.nflverse.com/{endpoint}{kind}.html'))
    (output / 'assets.json').write_text(json.dumps(assets, indent=2), encoding='utf-8')
    (output / 'discovery.json').write_text(json.dumps(discovery, indent=2), encoding='utf-8')
    return output


SCHEMA = '''
CREATE TABLE sources(id TEXT PRIMARY KEY,reference TEXT,sha256 TEXT,observed_at TEXT,path TEXT,kind TEXT,attribution TEXT,license TEXT);
CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
CREATE TABLE plays(game_id TEXT,play_id TEXT,season INTEGER,week INTEGER,game_date TEXT,posteam TEXT,defteam TEXT,pbp TEXT,game TEXT,base_source_ids TEXT,fields TEXT,PRIMARY KEY(game_id,play_id));
CREATE INDEX plays_offense ON plays(season,posteam);
CREATE INDEX plays_defense ON plays(season,defteam);
CREATE TABLE raw_records(source_id TEXT,row_number INTEGER,kind TEXT,season INTEGER,game_id TEXT,play_id TEXT,team TEXT,data TEXT,PRIMARY KEY(source_id,row_number));
CREATE TABLE player_crosswalk(pfr_id TEXT PRIMARY KEY,gsis_id TEXT,espn_id TEXT,source_id TEXT,data TEXT);
CREATE TABLE player_snaps(game_id TEXT,pfr_id TEXT,gsis_id TEXT,season INTEGER,week INTEGER,team TEXT,offense_snaps REAL,defense_snaps REAL,st_snaps REAL,source_id TEXT,crosswalk_source_id TEXT,data TEXT,PRIMARY KEY(game_id,team,pfr_id));
CREATE TABLE ngs_observations(kind TEXT,season INTEGER,season_type TEXT,week INTEGER,gsis_id TEXT,team TEXT,source_id TEXT,data TEXT,PRIMARY KEY(kind,season,season_type,week,gsis_id,team));
'''


def build(base, archive, output):
    base, archive, output = Path(base).resolve(), Path(archive).resolve(), Path(output).resolve()
    if base == output or output.exists():
        raise ValueError('overlay must be a NEW path, never an existing/base database')
    before = digest(base)
    manifest = json.loads((archive / 'assets.json').read_text(encoding='utf-8'))
    for item in manifest:
        if digest(archive / 'raw' / item['name']) != item['sha256']:
            raise ValueError(f'archive hash mismatch: {item["name"]}')
    source = sqlite3.connect(base.as_uri() + '?mode=ro', uri=True)
    source.row_factory = sqlite3.Row
    output.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(output)
    db.executescript(SCHEMA)
    source_map = {}
    def add_source(sid, reference, sha, observed, path, kind, attribution, license):
        db.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?,?,?,?,?,?)', (sid, reference, sha, observed, str(path), kind, attribution, license))
    for s in source.execute('SELECT * FROM sources ORDER BY id'):
        sid = 'charting:base:' + s['sha256']
        source_map[s['id']] = sid
    games = {g['id']: dict(g) for g in source.execute('SELECT * FROM games WHERE season BETWEEN 2024 AND 2026 ORDER BY id')}
    used_base_ids = set()
    keep = ('play_type', 'no_play', 'qb_dropback', 'pass_attempt', 'rush_attempt', 'sack', 'qb_scramble', 'qb_kneel', 'qb_spike', 'two_point_attempt', 'epa', 'qtr', 'score_differential')
    for p in source.execute('SELECT * FROM plays WHERE season BETWEEN 2024 AND 2026 ORDER BY game_id,play_id'):
        game = games.get(p['game_id'])
        if not game:
            continue
        if int(p['season']) != int(p['game_id'][:4]):
            raise ValueError('base season/game ID mismatch')
        data = json.loads(p['data'])
        ids = [p['source_id'], game['source_id']]
        used_base_ids.update(ids)
        db.execute('INSERT INTO plays VALUES(?,?,?,?,?,?,?,?,?,?,?)', (p['game_id'], canonical_id(p['play_id']), p['season'], game['week'], game['game_date'], p['posteam'], p['defteam'], json.dumps({k: data.get(k) for k in keep}, sort_keys=True), json.dumps({'home': game['home'], 'away': game['away']}, sort_keys=True), json.dumps(sorted({source_map[s] for s in ids})), '{}'))
    for s in source.execute('SELECT * FROM sources ORDER BY id'):
        if s['id'] in used_base_ids:
            add_source(source_map[s['id']], s['reference'], s['sha256'], s['observed_at'], s['path'], 'base:' + s['kind'], 'nflverse base PBP/game source', 'See original provider terms; no additional license grant inferred')
    crosswalk, crosswalk_sid = {}, None
    for item in manifest:
        if item['kind'] != 'players':
            continue
        crosswalk_sid = 'charting:' + item['sha256']
        for row in rows(archive / 'raw' / item['name']):
            pfr, gsis = row.get('pfr_id'), row.get('gsis_id')
            if pfr and gsis:
                if pfr in crosswalk and crosswalk[pfr]['gsis_id'] != gsis:
                    raise ValueError('ambiguous PFR crosswalk')
                crosswalk[pfr] = row
                db.execute('INSERT OR IGNORE INTO player_crosswalk VALUES(?,?,?,?,?)', (pfr, gsis, row.get('espn_id'), crosswalk_sid, json.dumps(row, sort_keys=True)))
    report = {'assets': [], 'join_failures': [], 'duplicates': [], 'requested_seasons': [2024, 2025, 2026]}
    fields_by_play = defaultdict(dict)
    for item in manifest:
        path, kind = archive / 'raw' / item['name'], item['kind']
        sid = 'charting:' + item['sha256']
        if path.suffix == '.parquet':
            continue  # exact CSV equivalents are parsed; both formats remain archived
        is_ftn = kind in ('ftn_charting', 'pbp_participation')
        add_source(sid, item['url'], item['sha256'], item['observed_at'], path, kind,
                   'FTN Data via nflverse' if is_ftn else 'NFL Next Gen Stats via nflverse' if kind == 'nextgen_stats' else 'Pro Football Reference via nflverse' if kind == 'snap_counts' else 'nflverse player identifiers',
                   'CC-BY-SA-4.0' if is_ftn else 'Provider terms apply; dataset-specific redistribution license not verified')
        count, counts, present, raw_fields = 0, Counter(), Counter(), Counter()
        seen = set()
        for line, row in enumerate(rows(path), 2):
            game_id = row.get('nflverse_game_id') or row.get('game_id')
            season = int(row.get('season') or (game_id or '0')[:4])
            if not 2024 <= season <= 2026:
                continue
            count += 1
            counts[season] += 1
            raw_fields.update(row.keys())
            present.update(k for k, v in row.items() if v not in ('', 'NA', 'NaN'))
            raw_play = row.get('nflverse_play_id') or row.get('play_id')
            play_id = None
            if raw_play:
                try:
                    play_id = canonical_id(raw_play)
                except ValueError:
                    pass
            db.execute('INSERT INTO raw_records VALUES(?,?,?,?,?,?,?,?)', (sid, line, kind, season, game_id, play_id, row.get('possession_team') or row.get('team') or row.get('team_abbr'), json.dumps(row, sort_keys=True)))
            if is_ftn:
                key = (game_id, play_id)
                if key in seen:
                    report['duplicates'].append({'source_id': sid, 'game_id': game_id, 'play_id': play_id})
                    raise ValueError('duplicate charting grain')
                seen.add(key)
                target = db.execute('SELECT season,posteam,defteam FROM plays WHERE game_id=? AND play_id=?', key).fetchone()
                if target is None or target[0] != season or row.get('possession_team') and row['possession_team'] != target[1]:
                    report['join_failures'].append({'source_id': sid, 'game_id': game_id, 'play_id': play_id, 'reason': 'No exact base play/team/season match'})
                    continue
                if game_id not in games or games[game_id]['season'] != season:
                    raise ValueError('charting season mismatch')
                selected = ('qb_location', 'n_offense_backfield', 'n_defense_box', 'is_motion', 'is_play_action', 'is_rpo', 'is_throw_away', 'n_blitzers', 'n_pass_rushers') if kind == 'ftn_charting' else ('offense_personnel', 'defense_personnel', 'defense_man_zone_type', 'defense_coverage_type', 'number_of_pass_rushers', 'was_pressure', 'time_to_throw')
                for field in selected:
                    value = row.get(field)
                    if value in (None, '', 'NA', 'NaN'):
                        continue
                    name = 'n_pass_rushers' if field == 'number_of_pass_rushers' else field
                    # Manifest order gives the manual subset priority; participation
                    # is only a fallback for pass-rusher counts, never a silent overwrite.
                    if name not in fields_by_play[key]:
                        fields_by_play[key][name] = {'value': value, 'source_ids': [sid]}
            elif kind == 'snap_counts':
                pfr = row['pfr_player_id']
                mapped = crosswalk.get(pfr)
                db.execute('INSERT INTO player_snaps VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (game_id, pfr, mapped['gsis_id'] if mapped else None, season, int(row['week']), row['team'], float(row['offense_snaps']) if row['offense_snaps'] else None, float(row['defense_snaps']) if row['defense_snaps'] else None, float(row['st_snaps']) if row['st_snaps'] else None, sid, crosswalk_sid if mapped else None, json.dumps(row, sort_keys=True)))
            elif kind == 'nextgen_stats':
                stat = item['name'].split('_')[1].split('.')[0]
                db.execute('INSERT INTO ngs_observations VALUES(?,?,?,?,?,?,?,?)', (stat, season, row['season_type'], int(row['week']), row['player_gsis_id'], row['team_abbr'], sid, json.dumps(row, sort_keys=True)))
        report['assets'].append({'source_id': sid, 'name': item['name'], 'rows_retained': count, 'seasons': dict(counts), 'fields': {k: {'present': present[k], 'rows': n, 'missing': n-present[k]} for k, n in raw_fields.items()}})
    db.executemany('UPDATE plays SET fields=? WHERE game_id=? AND play_id=?', ((json.dumps(data, sort_keys=True), *key) for key, data in sorted(fields_by_play.items())))
    metadata = {'schema_version': 1, 'base_input_path': str(base), 'base_input_sha256': before,
                'observed_at': datetime.now(timezone.utc).isoformat(), 'requested_seasons': [2024, 2025, 2026],
                'covered_seasons': sorted({int(s) for a in report['assets'] for s in a['seasons']}),
                'dataset_availability': {'ftn_charting': [2024, 2025, 2026], 'pbp_participation': [2024, 2025], 'snap_counts': [2024, 2025, 2026], 'nextgen_stats': [2024, 2025, 2026]},
                'release_lag': {'ftn_charting': 'Manual subset; provider says within 48 hours after games.', 'pbp_participation': '2023+ FTN full participation released after all postseason games; 2026 asset absent as expected, not a bad URL.'},
                'historically_pregame_certified': False, 'source_id_namespace': 'charting:',
                'definitions': 'docs/public-charting-sources.md', 'supported_metric_ids': sorted(RULES),
                'missingness': 'True zeros retained; unjoined rows quarantined in raw_records; missing and invalid fields never become zero.',
                'supported_views': ['season', 'last3', 'last5', 'last8', 'home', 'away', 'man', 'zone', 'blitz', 'four_man'],
                'ngs_grain': 'player/season/season_type/week/stat_type/team; week 0 is season summary, never pooled with weekly rows',
                'snap_grain': 'game/team/PFR player ID; GSIS joined only through exact nflverse players.csv PFR ID crosswalk; not pass-block/coverage snaps'}
    metadata['base_input_sha256_after'] = digest(base)
    if metadata['base_input_sha256_after'] != before:
        raise ValueError('base database changed during read')
    metadata['base_unchanged'] = True
    db.executemany('INSERT INTO metadata VALUES(?,?)', ((k, json.dumps(v, sort_keys=True)) for k, v in metadata.items()))
    db.commit()
    report['tables'] = {t: db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in ('sources', 'plays', 'raw_records', 'player_snaps', 'player_crosswalk', 'ngs_observations')}
    report['snap_crosswalk'] = {'total': report['tables']['player_snaps'], 'matched': db.execute('SELECT count(*) FROM player_snaps WHERE gsis_id IS NOT NULL').fetchone()[0]}
    db.close()
    source.close()
    report['metadata'] = metadata
    (output.parent / (output.stem + '-build-report.json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--collect', action='store_true')
    args = parser.parse_args()
    if args.collect:
        collect(args.archive)
    result = build(args.base, args.archive, args.output)
    print(json.dumps({'tables': result['tables'], 'snap_crosswalk': result['snap_crosswalk'], 'base_unchanged': result['metadata']['base_unchanged'], 'output': str(args.output)}, indent=2))
