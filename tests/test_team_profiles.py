import copy
import sqlite3
from dataclasses import replace
from datetime import timedelta

import pytest

from football_engine.team_profiles import TeamProfileStore, add_scheme_note, summarize, matchup_packet, bind_matchup, construct_parlay_family
from test_engine import NOW, snapshot, leg


def observations(game="1", available=NOW, yards=200, plays=40):
    own = dict(total_yards=yards, plays=plays, passing_attempts=20, sacks_taken=2,
               dropbacks_proxy=22, third_down_made=2, third_down_attempts=10)
    source = dict(reference="fixture:" + game, sha256=f"{game}:{yards}:{plays}", observed_at=available.isoformat())
    return [dict(team=team, opponent=other, game_id=game, week=1, season=2026,
                 kickoff=(NOW-timedelta(days=2)).isoformat(), available_at=available.isoformat(),
                 final=True, offense=own.copy(), opponent_offense=own.copy(), source=source.copy())
            for team, other in (("CHI", "SEA"), ("SEA", "CHI"))]


def test_pair_ingestion_is_immutable_idempotent_and_cutoff_safe(tmp_path):
    store = TeamProfileStore(tmp_path/"team.sqlite")
    rows = observations()
    store.add_game(rows)
    store.add_game(rows)
    assert store.connection.execute("SELECT COUNT(*) FROM team_observations").fetchone()[0] == 2
    assert store.profile("CHI", NOW-timedelta(seconds=1))["games"] == 0
    profile = store.profile("CHI", NOW)
    assert profile["games"] == 1
    assert profile["season"]["offense"]["sacks_per_dropback_proxy"]["value"] == 2/22
    assert profile["scheme"]["pressure_rate"]["value"] is None
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        store.connection.execute("UPDATE team_observations SET team='BAD'")


def test_correction_preserves_old_knowledge_and_ratio_uses_total_exposure(tmp_path):
    store = TeamProfileStore(tmp_path/"team.sqlite")
    store.add_game(observations())
    store.add_game(observations(available=NOW+timedelta(hours=1), yards=240))
    assert store.profile("CHI", NOW)["season"]["offense"]["yards_per_play"]["value"] == 5
    assert store.profile("CHI", NOW+timedelta(hours=2))["season"]["offense"]["yards_per_play"]["value"] == 6
    items = [observations(yards=200, plays=40)[0], observations("2", yards=300, plays=100)[0]]
    assert summarize(items)["offense"]["yards_per_play"]["value"] == 500/140
    assert summarize(items)["offense"]["passing_td_per_attempt"]["value"] is None


def test_nonfinal_or_unpaired_game_rejected_atomically(tmp_path):
    store = TeamProfileStore(tmp_path/"team.sqlite")
    rows = observations()
    rows[1]["final"] = False
    with pytest.raises(ValueError, match="final"):
        store.add_game(rows)
    assert store.connection.execute("SELECT COUNT(*) FROM team_observations").fetchone()[0] == 0
    with pytest.raises(ValueError, match="paired"):
        store.add_game(rows[:1])


def test_matchup_binding_invalidates_scenarios_and_rejects_altered_profiles(tmp_path):
    store = TeamProfileStore(tmp_path/"team.sqlite")
    store.add_game(observations())
    first, second = (store.profile(team, NOW) for team in ("CHI", "SEA"))
    packet = matchup_packet(first, second, NOW)
    bound = bind_matchup(snapshot(), packet)
    assert bound.team_profile_versions == packet["profile_versions"]
    assert not bound.scenarios
    assert all(not candidates for candidates in construct_parlay_family(bound, packet, (leg(),)).values())
    changed = copy.deepcopy(first)
    changed["games"] = 100
    with pytest.raises(ValueError, match="altered"):
        matchup_packet(changed, second, NOW)
    with pytest.raises(ValueError, match="bind"):
        construct_parlay_family(snapshot(), packet, (leg(),))


def test_scheme_notes_are_sourced_separate_and_versioned(tmp_path):
    store = TeamProfileStore(tmp_path/"team.sqlite")
    store.add_game(observations())
    old = store.profile("CHI", NOW)
    note = dict(team_id="CHI", status="MATCHUP_HYPOTHESIS", detail="Test interior matchup hypothesis",
                source=dict(reference="fixture:film", sha256="abc", observed_at=NOW.isoformat()))
    updated = add_scheme_note(old, note)
    assert updated["version"] != old["version"] and not old["scheme_notes"]
    assert updated["scheme"]["pressure_rate"]["value"] is None
    note["source"]["observed_at"] = (NOW+timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="future"):
        add_scheme_note(old, note)


def test_updated_team_evidence_invalidates_previously_bound_scenarios(tmp_path):
    store = TeamProfileStore(tmp_path/"team.sqlite")
    store.add_game(observations())
    first, second = (store.profile(team, NOW) for team in ("CHI", "SEA"))
    packet = matchup_packet(first, second, NOW)
    bound = replace(bind_matchup(snapshot(), packet), scenarios=snapshot().scenarios)
    assert bind_matchup(bound, packet).scenarios
    changed = add_scheme_note(first, dict(team_id="CHI", status="MATCHUP_HYPOTHESIS", detail="New film review",
        source=dict(reference="fixture:new", sha256="new", observed_at=NOW.isoformat())))
    new_packet = matchup_packet(changed, second, NOW)
    assert not bind_matchup(bound, new_packet).scenarios
