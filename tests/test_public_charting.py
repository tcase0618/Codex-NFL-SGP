import hashlib
import json
import sqlite3

import pytest

from football_engine.charting_metrics import ChartingMetrics, boolean, eligible, measure, personnel, RULES, team_charting_sources, team_charting_metadata
from research.public_charting import SCHEMA, build


@pytest.fixture
def overlay(tmp_path):
    path = tmp_path / 'charting.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        for sid, date in [('charting:base:fixture', '2026-10-01T00:00:00Z'), ('charting:manual', '2026-11-04T00:00:00Z')]:
            db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?)', (sid, 'https://example.test', sid, date, 'raw', 'test', 'fixture', 'test'))
        db.execute('INSERT INTO metadata VALUES(?,?)', ('base_input_sha256', json.dumps(hashlib.sha256(b'base').hexdigest())))
        for week in range(1, 5):
            game = f'2026_{week:02d}_SEA_SF'
            pbp = {'play_type': 'pass', 'qb_dropback': 1, 'epa': 0.5}
            fields = {k: {'value': v, 'source_ids': ['charting:manual']} for k, v in {'n_blitzers': '0' if week != 4 else '2', 'is_play_action': 'FALSE', 'is_motion': 'TRUE'}.items()}
            db.execute('INSERT INTO plays VALUES(?,?,?,?,?,?,?,?,?,?,?)', (game, '1', 2026, week, f'2026-10-{week:02d}', 'SEA', 'SF', json.dumps(pbp), json.dumps({'home': 'SF', 'away': 'SEA'}), json.dumps(['charting:base:fixture']), json.dumps(fields)))
    return ChartingMetrics(path)


def test_zero_and_missing_are_distinct():
    assert boolean('FALSE') == 0
    assert boolean('') is None


def test_explicit_personnel():
    assert personnel('1 QB, 5 OL, 1 RB, 1 TE, 3 WR')['RB'] == 1
    assert personnel('1 RB, 1 TE, 3 WR') is None


def test_special_teams_never_dilute_blitz():
    assert not eligible({'play_type': 'kickoff', 'qb_dropback': 0}, 'dropback')
    assert not eligible({'play_type': 'pass', 'qb_dropback': 1, 'no_play': 1}, 'dropback')


def test_implausible_blitz_is_missing():
    fields = {'n_blitzers': {'value': '52', 'source_ids': ['charting:manual']}}
    assert measure(fields, {}, RULES['coverage.blitz_frequency']) == (None, None)


def test_cutoff_withholds_manual_fields(overlay):
    value = overlay.value('SF', 2026, 'coverage.blitz_frequency', as_of='2026-11-03T00:00:00Z')
    assert value['value'] is None
    assert value['missing_opportunities'] == 4
    assert value['source_ids'] == []


def test_cutoff_withholds_base_dependencies(overlay):
    value = overlay.value('SF', 2026, 'coverage.blitz_frequency', as_of='2026-09-01T00:00:00Z')
    assert value['value'] is None
    assert value['sample_plays'] == 0


def test_true_zero_and_source_namespace(overlay):
    value = overlay.value('SEA', 2026, 'scheme.play_action_frequency')
    assert value['value'] == 0
    assert value['sample_plays'] == 4
    assert all(s.startswith('charting:') for s in value['source_ids'])


def test_last_n_and_week_filters(overlay):
    assert overlay.value('SF', 2026, 'coverage.blitz_frequency')['value'] == 25
    assert overlay.value('SF', 2026, 'coverage.blitz_frequency', 'last3')['value'] == pytest.approx(33.33333)
    assert overlay.value('SF', 2026, 'coverage.blitz_frequency', week_max=3)['value'] == 0


def test_historical_season_never_relabelled(overlay):
    assert overlay.value('SF', 2025, 'coverage.blitz_frequency')['value'] is None
    assert overlay.value('SF', 2026, 'coverage.man_coverage_frequency')['value'] is None


def test_unsupported_no_substitution(overlay):
    assert overlay.value('SEA', 2026, 'scheme.motion_at_snap') is None
    assert overlay.value('SF', 2026, 'coverage.blitz_frequency', 'opponent_adjusted') is None


def test_base_fingerprint_mismatch_raises(overlay, tmp_path):
    base = tmp_path / 'base.sqlite'
    base.write_bytes(b'wrong')
    with pytest.raises(ValueError, match='SHA256'):
        overlay.verify_base(base)
    base.write_bytes(b'base')
    assert overlay.verify_base(base)


def test_aggregate_evidence_exact_value_and_sources(overlay):
    evidence = overlay.evidence('SF', 2026, 'coverage.blitz_frequency')
    assert evidence['aggregate']['value'] == 25
    assert evidence['total'] == 4
    assert evidence['row_evidence_available'] is False
    assert {s['id'] for s in evidence['sources']} == set(evidence['source_ids'])
    assert team_charting_sources(overlay.path)
    assert 'base_input_sha256' in team_charting_metadata(overlay.path)


def test_never_overwrite_existing_snapshot(tmp_path):
    base = tmp_path / 'base.sqlite'
    base.write_bytes(b'existing')
    with pytest.raises(ValueError, match='NEW'):
        build(base, tmp_path, base)
    assert base.read_bytes() == b'existing'
