"""Independent event edge cases and an end-to-end immutable snapshot fixture."""
import gzip
import hashlib
import io
import json
import sqlite3

import pytest

from football_engine.player_metrics import CATALOG, PLAY_METRICS
from football_engine.player_profile_store import PlayerProfileStore
from research.enrich_player_metrics import contributions, context_matches, run, sha256, splits

QB = '00-0000001'
WR = '00-0000002'


def play(**changes):
    return dict(passer_player_id=QB, receiver_player_id=WR, pass_attempt=1, sack=0,
                qb_dropback=1, complete_pass=1, qb_scramble=0, rush_attempt=0,
                qb_kneel=0, qb_spike=0, special_teams_play=0, two_point_attempt=0,
                play_type='pass', epa=0, success=0, pass_touchdown=0,
                yards_gained=20, receiving_yards=20, air_yards=15, qtr=1,
                down=3, yardline_100=20, half_seconds_remaining=120,
                posteam_type='home', posteam='AAA', **changes)


def values(data):
    return {(pid, mid): (n, d) for pid, mid, n, d in contributions(data)}


def test_true_zero_missing_and_threshold_boundary_are_distinct():
    row = play(); result = values(row)
    assert result[QB, 'qb.epa_dropback'] == (0, 1)
    assert result[QB, 'qb.passing_success_rate'] == (0, 1)
    assert result[WR, 'wr.explosive_reception'] == (1, 1)
    row['epa'] = None
    assert (QB, 'qb.epa_dropback') not in values(row)
    assert values(row)[WR, 'wr.explosive_reception'] == (1, 1)
    row['receiving_yards'] = 19
    assert values(row)[WR, 'wr.explosive_reception'] == (0, 1)


@pytest.mark.parametrize('field', ['qb_kneel', 'qb_spike', 'special_teams_play', 'two_point_attempt'])
def test_excluded_events_never_supply_efficiency(field):
    data = play(); data[field] = 1
    assert not contributions(data)


def test_sacks_and_scrambles_use_separate_explicit_opportunities():
    data = play(); data.update(sack=1, epa=-2, receiver_player_id=None)
    actual = values(data)
    assert actual[QB, 'qb.epa_dropback'] == (-2, 1)
    assert (QB, 'qb.passing_success_rate') not in actual
    data.update(sack=0, pass_attempt=0, qb_scramble=1, rush_attempt=1,
                rusher_player_id=QB, rushing_yards=10, passer_player_id=QB)
    triples = contributions(data)
    assert sum(pid == QB and mid == 'qb.epa_play' for pid, mid, _, _ in triples) == 1
    assert values(data)[QB, 'qb.scramble_rate'] == (1, 1)
    data['passer_player_id'] = None
    assert values(data)[QB, 'qb.epa_dropback'] == (-2, 1)
    data['rusher_player_id'] = None
    assert not contributions(data)


def test_missing_identity_never_matches_names_and_context_is_bounded():
    data = play(); data.update(passer_player_id=None, receiver_player_id=None, passer_player_name='Known Name')
    assert not contributions(data)
    assert context_matches(play(), 'red_zone')
    assert context_matches(play(), 'two_minute')
    data = play(); data.update(yardline_100=-1, half_seconds_remaining=None)
    assert not context_matches(data, 'red_zone')
    assert not context_matches(data, 'two_minute')
    data['qtr'] = 5
    assert 'second_half' not in splits(data)
    assert len({m['id'] for group in CATALOG.values() for m in group}) == 615
    assert set(PLAY_METRICS) <= {m['id'] for group in CATALOG.values() for m in group}


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    base, teams = tmp_path / 'base.sqlite', tmp_path / 'teams.sqlite'
    with sqlite3.connect(base) as db:
        db.executescript('''CREATE TABLE players(id TEXT PRIMARY KEY,position TEXT);
            CREATE TABLE sources(id INTEGER PRIMARY KEY,reference TEXT,sha256 TEXT,observed_at TEXT,kind TEXT,path TEXT);
            CREATE TABLE observations(player_id TEXT,season INTEGER,week INTEGER,game_type TEXT,kind TEXT,data TEXT,source_id INTEGER,PRIMARY KEY(player_id,season,week,game_type,kind));
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
            INSERT INTO metadata VALUES('summary','{}');
            CREATE TRIGGER observation_no_update BEFORE UPDATE ON observations BEGIN SELECT RAISE(ABORT,'immutable'); END;''')
        db.executemany('INSERT INTO players VALUES(?,?)', [(QB, 'QB'), (WR, 'WR')])
        db.execute('INSERT INTO sources VALUES(1,?,?,?,?,?)', ('fixture', 'a'*64, '2026-11-01T00:00:00Z', 'stats', 'fixture'))
        for week in range(1, 5):
            db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?)', (QB, 2026, week, 'REG', 'stats', json.dumps({'attempts':1,'completions':1}), 1))
    raw = tmp_path / 'play_by_play_2026.csv.gz'
    raw.write_bytes(gzip.compress(b'explicit offline fixture raw bytes'))
    with sqlite3.connect(teams) as db:
        db.executescript('''CREATE TABLE sources(id INTEGER PRIMARY KEY,reference TEXT,sha256 TEXT,observed_at TEXT,path TEXT,kind TEXT);
            CREATE TABLE plays(game_id TEXT,play_id TEXT,season INTEGER,source_id INTEGER,data TEXT,PRIMARY KEY(game_id,play_id));
            CREATE TABLE games(id TEXT PRIMARY KEY,week INTEGER,game_date TEXT);''')
        db.execute('INSERT INTO sources VALUES(1,?,?,?,?,?)', ('https://example.test/fixture', sha256(raw), '2026-11-01T00:00:00Z', str(raw), 'pbp'))
        db.execute('INSERT INTO games VALUES("2026_01_AAA_BBB",1,"2026-09-10")')
        for index, (epa, success, yards) in enumerate([(0, 0, 20), (2, 1, 0), (None, 1, 5)]):
            data = play(); data.update(epa=epa, success=success, yards_gained=yards, receiving_yards=yards)
            db.execute('INSERT INTO plays VALUES(?,?,?,?,?)', ('2026_01_AAA_BBB', str(index), 2026, 1, json.dumps(data)))
    monkeypatch.setattr('urllib.request.urlopen', lambda *args, **kwargs: io.BytesIO(b'<html>offline definition fixture</html>'))
    before = [sha256(base), sha256(teams)]
    output = tmp_path / 'new-v5'
    report = run(base, teams, output)
    assert before == [sha256(base), sha256(teams)]
    return base, teams, output, report


def test_new_backup_snapshot_uses_measured_denominators_and_preserves_immutability(snapshot):
    base, teams, output, report = snapshot
    store = PlayerProfileStore(output / 'players.sqlite')
    def metric(mid, **kwargs):
        definition = next(m for m in CATALOG[mid.split('.')[0].upper()] if m['id'] == mid)
        return store.metric(QB, definition, 2026, **kwargs)
    assert metric('qb.epa_dropback')['value'] == 1
    assert metric('qb.epa_dropback')['sample_opportunities'] == 2
    assert metric('qb.passing_success_rate')['value'] == 66.6667
    assert metric('qb.epa_dropback', view='last3')['value'] is None
    assert metric('qb.epa_dropback', as_of='2026-01-01T00:00:00Z')['value'] is None
    assert metric('qb.epa_dropback', view='first_half')['value'] == 1
    assert metric('qb.pressure_grade')['value'] is None
    assert metric('qb.epa_dropback', view='pressure')['value'] is None
    assert report['bases_unchanged'] and report['integrity_check'] == 'ok'
    assert report['covered_seasons'] == report['source_years'] == [2026]
    assert sum(row['added_slots'] for row in report['coverage_by_season_position']) > 0
    with sqlite3.connect(output / 'players.sqlite') as db:
        with pytest.raises(sqlite3.IntegrityError): db.execute('UPDATE observations SET data="{}"')
    with pytest.raises(FileExistsError): run(base, teams, output)
    hashes = json.loads((output / 'sha256.json').read_text())
    assert hashes['players.sqlite'] == sha256(output / 'players.sqlite')
    lineage = json.loads((output / 'lineage.json').read_text())
    assert sha256(lineage['sources'][0]['path']) == lineage['sources'][0]['sha256']

def test_percentile_uses_derived_peers_and_keeps_source_cutoff(snapshot):
    _, _, output, _ = snapshot
    peer = '00-0000003'
    with sqlite3.connect(output / 'players.sqlite') as db:
        db.execute('INSERT INTO players VALUES(?,?)', (peer, 'QB'))
        db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?)',
                   (peer, 2026, 1, 'REG', 'pbp_season', json.dumps({'game_id':'fixture-peer','qb.epa_dropback:n':4,'qb.epa_dropback:d':2}), 2))
    store = PlayerProfileStore(output / 'players.sqlite')
    metric = next(m for m in CATALOG['QB'] if m['id'] == 'qb.epa_dropback')
    result = store.metric(QB, metric, 2026, 'percentile')
    assert result['value'] == 25 and result['peer_count'] == 2 and result['raw_value'] == 1
    assert store.metric(QB, metric, 2026, 'percentile', as_of='2026-01-01T00:00:00Z')['value'] is None


def test_touchdown_per_target_weekly_and_pbp_have_same_percent_scale(snapshot):
    _, _, output, _ = snapshot
    metric = next(m for m in CATALOG['WR'] if m['id'] == 'wr.touchdown_target')
    assert metric['unit'] == '%'
    assert PLAY_METRICS[metric['id']]['scale'] == 100
    # Ten explicit targeted plays, exactly one observed passing touchdown.
    totals = [values(dict(play(), pass_touchdown=int(i == 0)))[WR, metric['id']] for i in range(10)]
    payload = {'game_id': 'fixture-wr', metric['id'] + ':n': sum(n for n, _ in totals),
               metric['id'] + ':d': sum(d for _, d in totals)}
    with sqlite3.connect(output / 'players.sqlite') as db:
        db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?)',
                   (WR, 2025, 1, 'REG', 'stats', json.dumps({'targets':10,'receiving_tds':1}), 1))
        db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?)',
                   (WR, 2025, 1, 'REG', 'pbp_season', json.dumps(payload), 2))
    store = PlayerProfileStore(output / 'players.sqlite')
    weekly = store.metric(WR, metric, 2025)
    fallback = store.play_metric(WR, metric, 2025)
    assert weekly['value'] == fallback['value'] == 10
    assert weekly['unit'] == fallback['unit'] == '%'
    assert weekly['sample_opportunities'] == fallback['sample_opportunities'] == 10
    assert '100' in fallback['formula']


def test_empty_summary_gets_observation_counts_without_keyerror(snapshot):
    _, _, output, report = snapshot
    # The fixture intentionally starts with metadata.summary = {}.
    with sqlite3.connect(output / 'players.sqlite') as db:
        summary = json.loads(db.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()[0])
    assert summary['observations']['pbp_derived'] == report['derived_rows'] > 0
    assert summary['latest_season'] == 2026

@pytest.fixture
def supplemental_store(tmp_path):
    base = tmp_path / 'players.sqlite'
    charting = tmp_path / 'charting.sqlite'
    with sqlite3.connect(base) as db: db.execute('CREATE TABLE players(id TEXT)')
    with sqlite3.connect(charting) as db:
        db.executescript('''CREATE TABLE sources(id TEXT PRIMARY KEY,reference TEXT,sha256 TEXT,observed_at TEXT);
        CREATE TABLE player_crosswalk(pfr_id TEXT PRIMARY KEY,gsis_id TEXT,source_id TEXT);
        CREATE TABLE player_snaps(game_id TEXT,pfr_id TEXT,gsis_id TEXT,season INTEGER,week INTEGER,team TEXT,offense_snaps REAL,defense_snaps REAL,st_snaps REAL,source_id TEXT,crosswalk_source_id TEXT,data TEXT);
        CREATE TABLE ngs_observations(kind TEXT,season INTEGER,season_type TEXT,week INTEGER,gsis_id TEXT,team TEXT,source_id TEXT,data TEXT);''')
        db.executemany('INSERT INTO sources VALUES(?,?,?,?)', [('snaps','https://example.test/snaps','a'*64,'2026-11-01T00:00:00Z'),('identity','https://example.test/ids','b'*64,'2026-11-02T00:00:00Z'),('ngs','https://example.test/ngs','c'*64,'2026-11-01T00:00:00Z')])
        db.executemany('INSERT INTO player_crosswalk VALUES(?,?,?)',[('explicit',QB,'identity'),('conflict',WR,'identity')])
        for gid, pfr, year, week, offense, defense, st, game_type in [('g1','explicit',2026,1,0,50,10,'REG'),('g2','explicit',2026,2,None,0,5,'REG'),('g3','conflict',2026,3,99,0,0,'REG'),('post','explicit',2026,4,99,0,0,'POST'),('prior','explicit',2024,1,99,0,0,'REG')]:
            db.execute('INSERT INTO player_snaps VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(gid,pfr,QB,year,week,'AAA',offense,defense,st,'snaps','identity',json.dumps({'game_type':game_type})))
        for week,value in [(0,2.5),(1,2.1)]:
            db.execute('INSERT INTO ngs_observations VALUES(?,?,?,?,?,?,?,?)',('passing',2026,'REG',week,QB,'AAA','ngs',json.dumps({'player_gsis_id':QB,'avg_time_to_throw':value,'attempts':10})))
        db.execute('INSERT INTO ngs_observations VALUES(?,?,?,?,?,?,?,?)',('passing',2026,'REG',2,QB,'AAA','ngs',json.dumps({'player_gsis_id':WR,'avg_time_to_throw':9})))
    return PlayerProfileStore(base, charting_path=charting), base, charting


def test_supplemental_snaps_preserve_zero_missing_identity_and_exact_year(supplemental_store):
    store, base, charting = supplemental_store
    hashes = [sha256(base),sha256(charting)]
    result = store.supplemental_context(QB,2026)
    assert result['status'] == 'OBSERVED_PUBLIC_CONTEXT'
    assert len(result['snaps']) == 2
    assert result['snaps'][0]['offense_snaps'] == 0
    assert result['snaps'][0]['total_snaps'] == 60
    assert result['snaps'][1]['offense_snaps'] is None
    assert result['snaps'][1]['total_snaps'] is None
    assert result['snap_totals']['offense_snaps'] == {'value':0,'observed_rows':1,'missing_rows':1}
    assert result['snap_totals']['total_snaps'] == {'value':60,'observed_rows':1,'missing_rows':1}
    assert {s['id'] for s in result['sources']} == {'snaps','identity','ngs'}
    assert all(sid in {s['id'] for s in result['sources']} for row in result['snaps']+result['ngs'] for sid in row['source_ids'])
    assert len(store.supplemental_context(QB,2024)['snaps']) == 1
    assert not store.supplemental_context(QB,2025)['snaps']
    assert [sha256(base),sha256(charting)] == hashes


def test_supplemental_ngs_aggregate_not_double_counted_or_backdated(supplemental_store):
    store, _, _ = supplemental_store
    result = store.supplemental_context(QB,2026)
    assert [r['grain'] for r in result['ngs']] == ['provider_season_aggregate','player_week_team']
    assert result['ngs'][0]['fields']['avg_time_to_throw'] == 2.5
    early = store.supplemental_context(QB,2026,as_of='2026-11-01T12:00:00Z')
    assert not early['snaps'] and len(early['ngs']) == 2
    hidden = store.supplemental_context(QB,2026,as_of='2026-10-01T00:00:00Z')
    assert hidden['status']=='UNAVAILABLE' and hidden['sources']==[]
    bounded = store.supplemental_context(QB,2026,week_max=1)
    assert len(bounded['snaps'])==len(bounded['ngs'])==1
    assert bounded['ngs'][0]['week']==1
    with pytest.raises(ValueError,match='timezone'):store.supplemental_context(QB,2026,as_of='2026-11-01')
    assert PlayerProfileStore(store.path).supplemental_context(QB,2026)['status']=='UNAVAILABLE'
