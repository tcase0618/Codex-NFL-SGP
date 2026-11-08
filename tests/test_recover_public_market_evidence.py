"""Synthetic fixtures test evidence boundaries, never historical performance."""
import json
from types import SimpleNamespace

import pytest
import requests

from research import recover_public_market_evidence as recovery


def test_body_and_error_status_are_preserved_without_historical_promotion(tmp_path):
    raw = b'<html>Access denied</html>'
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(content=raw, url=url, status_code=403,
                               headers={'Date': 'Tue, 03 Nov 2026 00:00:00 GMT'}, history=[])

    record = recovery.fetch_probe(('bookmaker_past_event', 'https://example.invalid/event/1'), tmp_path, get)
    assert len(calls) == 1
    assert record['response_status'] == 'HTTP_ERROR_BODY_ARCHIVED'
    assert recovery.Path(record['path']).read_bytes() == raw
    assert record['sha256'] == recovery.sha(raw)
    assert record['response_headers']['Date'].startswith('Tue')
    assert record['historical_version_verified'] is False
    assert record['recovered_historical_player_quotes'] is False
    assert record['discovered_at'] >= record['request_started_at']
    with pytest.raises(FileExistsError):
        recovery.fetch_probe(('bookmaker_past_event', 'https://example.invalid/event/1'), tmp_path, get)


def test_transport_failure_is_one_attempt_without_fabricated_response(tmp_path):
    calls = []

    def get(*args, **kwargs):
        calls.append(args)
        raise requests.Timeout('synthetic timeout')

    record = recovery.fetch_probe(('bookmaker_past_event', 'https://example.invalid'), tmp_path, get)
    assert len(calls) == 1
    assert record['response_status'] == 'TRANSPORT_ERROR_NO_BODY'
    assert 'sha256' not in record
    assert list(tmp_path.iterdir()) == []


def test_original_publication_and_active_text_never_certify_pregame_version():
    raw = b'''<script type="application/ld+json">{"@graph":[
    {"@type":"NewsArticle","datePublished":"2026-09-09T22:51:00Z",
     "dateModified":"2026-11-03T00:00:00Z"}]}</script>
    <div class="story-part-rich-text-editor-wrapper">
    <p>Synthetic Player is active tonight.</p><p>Another Player is inactive.</p></div>'''
    claims = recovery.article_claims(raw)
    assert claims['source_claimed_published_at'] == '2026-09-09T22:51:00Z'
    assert claims['source_claimed_modified_at'] == '2026-11-03T00:00:00Z'
    assert claims['explicit_active_text_leads'] == ['Synthetic Player is active tonight.']
    assert claims['historical_version_verified'] is False
    assert claims['complete_active_universe_verified'] is False
    assert 'players' not in claims


def test_hash_mismatch_is_visible_and_file_unchanged(tmp_path):
    path = tmp_path / 'source.body'
    path.write_bytes(b'changed')
    record = recovery.file_evidence(path, recovery.sha(b'original'))
    assert record['integrity'] == 'MISMATCH'
    assert path.read_bytes() == b'changed'
    assert recovery.file_evidence(path)['integrity'] == 'HASHED_NOW_NO_PRIOR_CONTENT_HASH'


def test_final_participation_and_team_odds_cannot_become_player_quotes():
    data = {'header': {'id': '1'}, 'pickcenter': [
        {'provider': {'name': 'synthetic book'}, 'spread': -3, 'overUnder': 44.5,
         'homeTeamOdds': {'moneyLine': -170, 'teamId': '26'}}],
        'boxscore': {'players': [{'active': True}]}, 'rosters': [{'didNotPlay': False}]}
    rows = recovery.team_markets(data, '1')
    assert len(rows) == 1
    assert rows[0]['historical_version_verified'] is False
    assert rows[0]['quote_published_at'] is None
    assert 'TEAM_MARKETS_ONLY' in rows[0]['scope']
    assert 'active' not in json.dumps(rows)
    with pytest.raises(ValueError, match='game ID mismatch'):
        recovery.team_markets(data, '2')


@pytest.mark.parametrize('value', [True, None, float('nan'), float('inf'), '-3'])
def test_non_numeric_team_line_does_not_count_as_coverage(value):
    assert recovery.team_markets({'header': {'id': '1'}, 'pickcenter': [{'spread': value}]}, '1') == []


def test_existing_output_is_refused_before_reads_or_network(tmp_path, monkeypatch):
    monkeypatch.setattr(recovery.requests, 'get', lambda *a, **kw: pytest.fail('network must not run'))
    with pytest.raises(FileExistsError):
        recovery.run(tmp_path / 'nonexistent-workspace', tmp_path)


def test_missing_archive_has_no_fake_digest(tmp_path):
    record = recovery.file_evidence(tmp_path / 'missing')
    assert record['integrity'] == 'UNREADABLE'
    assert 'sha256' not in record


def test_club_article_body_layout_extracts_text_only():
    claims = recovery.article_claims(b'<div class="nfl-c-body-part--text"><p>Fixture Player is also active.</p></div>')
    assert claims['explicit_active_text_leads'] == ['Fixture Player is also active.']
    assert claims['complete_active_universe_verified'] is False


def test_full_collection_preserves_source_and_keeps_all_gates_blocked(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    (workspace / 'outputs/reconstructed-replay-20261103-v4').mkdir(parents=True)
    (workspace / 'work/espn-cache').mkdir(parents=True)
    ticket = {'game_id': '1', 'game': 'Synthetic A at Synthetic B', 'week': 1,
              'kickoff': '2026-09-10T00:20Z', 'decision_cutoff': '2026-09-09T23:50Z'}
    recovery.save_json(workspace / 'outputs/reconstructed-replay-20261103-v4/selection-manifest.json', {'tickets': [ticket]})
    original = workspace / 'original.html'
    original.write_bytes(b'synthetic inactive archive')
    recovery.save_json(workspace / 'outputs/pregame-source-inventory.json', {
        'sources': [{'reference': 'https://example.invalid', 'html_file': str(original),
                     'content_sha256': recovery.sha(original.read_bytes())}],
        'game_coverage': [{'game_id': '1', 'sources': ['https://example.invalid'], 'inactive_team_sections': 2}]})
    url = recovery.BASE + '/summary?event=1'
    recovery.save_json(workspace / 'work/espn-cache' / (recovery.sha(url.encode()) + '.json'),
                       {'header': {'id': '1'}, 'pickcenter': [{'spread': -3}]})
    monkeypatch.setattr(recovery, 'PROBES', ())
    result = recovery.run(workspace, tmp_path / 'new-output')
    manifest = json.loads((tmp_path / 'new-output/coverage-manifest.json').read_bytes())
    assert result['games_with_cached_team_market_observations'] == 1
    assert result['games_with_hash_matched_inactive_sources'] == 1
    assert result['verified_complete_pregame_active_universe_games'] == 0
    assert result['verified_player_prop_quote_games'] == 0
    assert result['readiness'] == 'BLOCKED'
    assert manifest['games'][0]['exact_combined_ticket_quotes_verified'] is False
    assert original.read_bytes() == b'synthetic inactive archive'
