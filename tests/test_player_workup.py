"""Synthetic workup fixtures exercise evidence gates, not actual NFL status."""

from copy import deepcopy
from datetime import timedelta
from hashlib import sha256

from football_engine.player_workup import verify_player_workups
from test_engine import NOW, leg, snapshot


def fixture_workup(tmp_path, game, offered):
    entry = {
        'player_id': offered.player_id, 'team_id': offered.team_id,
        'game_id': game.game_id, 'as_of': game.as_of.isoformat(),
        'reviewed_at': game.as_of.isoformat(), 'reviewer_reference': 'fixture:review',
    }
    artifacts = []
    for kind, finding in [('roster', 'on_team_roster'),
                          ('injury', 'not_listed_on_complete_report'),
                          ('transactions', 'same_team_through_cutoff'),
                          ('active', 'confirmed_active')]:
        path = tmp_path / f'{offered.player_id}-{kind}.html'
        path.write_text(f'fixture {offered.player_id} {offered.team_id} {kind}', encoding='utf-8')
        digest = sha256(path.read_bytes()).hexdigest()
        artifacts.append({'path': str(path), 'sha256': digest})
        entry[kind] = {
            'player_id': offered.player_id, 'team_id': offered.team_id,
            'reference': f'https://www.nfl.com/fixture/{kind}',
            'observed_at': game.as_of.isoformat(), 'path': str(path),
            'sha256': digest, 'finding': finding,
        }
    entry['injury']['complete_team_report'] = True
    report = {'auditors': {'roster': {'evidence': {'player_workups': {offered.player_id: entry}}}},
              'verified_artifacts': artifacts}
    return report


def test_each_player_requires_injury_team_transactions_and_positive_active_archive(tmp_path):
    game, offered = snapshot(), leg()
    report = fixture_workup(tmp_path, game, offered)
    assert verify_player_workups(game, (offered,), report) == ()
    missing = deepcopy(report)
    del missing['auditors']['roster']['evidence']['player_workups'][offered.player_id]['injury']
    assert any('injury archive missing' in reason for reason in verify_player_workups(game, (offered,), missing))
    wrong_team = deepcopy(report)
    wrong_team['auditors']['roster']['evidence']['player_workups'][offered.player_id]['team_id'] = 'SEA'
    assert any('team' in reason for reason in verify_player_workups(game, (offered,), wrong_team))
    inactive = deepcopy(report)
    inactive['auditors']['roster']['evidence']['player_workups'][offered.player_id]['active']['finding'] = 'not_on_inactive_list'
    assert any('active finding' in reason for reason in verify_player_workups(game, (offered,), inactive))


def test_stale_tampered_and_questionable_workups_block(tmp_path):
    game, offered = snapshot(), leg()
    report = fixture_workup(tmp_path, game, offered)
    entry = report['auditors']['roster']['evidence']['player_workups'][offered.player_id]
    entry['injury']['finding'] = 'questionable'
    assert any('injury designation' in reason for reason in verify_player_workups(game, (offered,), report))
    entry['injury']['finding'] = 'not_listed_on_complete_report'
    entry['active']['observed_at'] = (NOW - timedelta(hours=7)).isoformat()
    assert any('active source is future, stale' in reason for reason in verify_player_workups(game, (offered,), report))
    entry['active']['observed_at'] = NOW.isoformat()
    from pathlib import Path
    Path(entry['roster']['path']).write_text('changed after audit', encoding='utf-8')
    assert any('roster archive hash mismatch' in reason for reason in verify_player_workups(game, (offered,), report))


def test_one_workup_must_cover_each_unique_player(tmp_path):
    game, offered = snapshot(), leg()
    report = fixture_workup(tmp_path, game, offered)
    second = type(offered)('p2', offered.team_id, offered.market, offered.line, 0, 2, offered.source)
    assert any('exactly cover every offered player' in reason for reason in
               verify_player_workups(game, (offered, second), report))
