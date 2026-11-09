import hashlib
import json
from dataclasses import replace
from datetime import timedelta

from football_engine.construction import construct_audited_tickets
from football_engine.engine import Source
from football_engine.market_first import verify_offered_pool
from test_engine import leg, snapshot


def capture(tmp_path, *, change=None):
    game = snapshot()
    source = Source('FanDuel', 'https://sportsbook.fanduel.com/', game.as_of)
    offered = replace(leg(), source=source)
    data = {
        'schema': 'FANDUEL_TN_OFFER_CAPTURE_V1', 'book': 'FanDuel',
        'jurisdiction': 'TN', 'game_id': game.game_id,
        'kickoff': game.kickoff.isoformat(),
        'source': {'reference': source.reference, 'observed_at': source.observed_at.isoformat()},
        'offers': [{'offer_id': 'market-1', 'player_id': offered.player_id,
                    'team_id': offered.team_id, 'market': offered.market,
                    'line': offered.line, 'odds_decimal': offered.odds_decimal}],
    }
    if change:
        change(data)
    path = tmp_path / 'offers.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    return game, offered, path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_market_first_requires_frozen_exact_offer(tmp_path):
    game, offered, path, digest = capture(tmp_path)
    assert verify_offered_pool(game, (offered,), path, expected_sha256=digest).status == 'PASS'
    assert verify_offered_pool(game, (offered,), None).status == 'BLOCKED'
    assert verify_offered_pool(game, (offered,), path, expected_sha256='0' * 64).status == 'BLOCKED'
    assert verify_offered_pool(game, (replace(offered, line=25),), path,
                               expected_sha256=digest).status == 'BLOCKED'
    assert verify_offered_pool(game, (replace(offered, odds_decimal=5),), path,
                               expected_sha256=digest).status == 'BLOCKED'


def test_market_first_rejects_wrong_source_scope_and_stale_capture(tmp_path):
    for change in (
        lambda d: d.update(book='Other'),
        lambda d: d.update(jurisdiction='NY'),
        lambda d: d.update(game_id='different'),
        lambda d: d['source'].update(reference='https://fanduel.com.evil.example/'),
        lambda d: d['source'].update(observed_at=(snapshot().as_of - timedelta(minutes=6)).isoformat()),
        lambda d: d['offers'][0].update(offer_id=''),
    ):
        game, offered, path, digest = capture(tmp_path, change=change)
        assert verify_offered_pool(game, (offered,), path, expected_sha256=digest).status == 'BLOCKED'


def test_audited_entry_blocks_before_selection_without_market_capture():
    result = construct_audited_tickets(snapshot(), (leg(),), 3,
                                       accuracy_report=None, model_version='m')
    assert result.status == 'BLOCKED'
    assert 'market capture required' in result.reasons[0]
    assert result.candidates == ()


def test_late_revision_cannot_reuse_a_fresh_quote_after_decision_cutoff(tmp_path):
    game, offered, path, digest = capture(tmp_path)
    late_time = game.kickoff - timedelta(minutes=27)
    late_game = replace(game, as_of=late_time)
    late_offer = replace(offered, source=replace(offered.source, observed_at=late_time))
    data = json.loads(path.read_text(encoding='utf-8'))
    data['source']['observed_at'] = late_time.isoformat()
    path.write_text(json.dumps(data), encoding='utf-8')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    result = verify_offered_pool(late_game, (late_offer,), path, expected_sha256=digest)
    assert result.status == 'BLOCKED'
    assert 'decision cutoff' in result.reasons[0]
