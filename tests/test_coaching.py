import copy
from datetime import timedelta

import pytest
from bs4 import BeautifulSoup

from football_engine.coaching import attach_coaching, coaching_matchup_context
from football_engine.team_profiles import TeamProfileStore, matchup_packet, bind_matchup
from research.collect_coaching import staff_cards
from test_engine import NOW, snapshot
from test_team_profiles import observations


def context(team="CHI"):
    source = dict(reference="fixture:coaching", sha256="coaching-hash", observed_at=NOW.isoformat())
    staff = {role: dict(name=name, title=role, status="CURRENT_DIRECTORY", sources=[source]) for role, name in
             (("head_coach", "HC"), ("offensive_coordinator", "OC"), ("defensive_coordinator", "DC"))}
    staff["head_coach"]["new_head_coach_2026"] = True
    return dict(team_id=team, staff=staff, directory_verified_at=NOW.isoformat(),
                play_callers={"offense": dict(name="HC",status="CONFIRMED",sources=[source]),
                              "defense": dict(name=None,status="UNCONFIRMED",sources=[source])},
                head_coach_management=dict(description="Fixture leadership",sources=[source]),
                offense=dict(description="Fixture run/pass plan",scope="REPORTED_2026_PLAN",sources=[source]),
                defense=dict(description="Fixture coverage plan",scope="REPORTED_2026_PLAN",sources=[source]),
                changes=[], era_start_dates=dict(offense="2026-10-31",defense=None), reported_rates=[])


def test_coaching_change_versions_profile_and_invalidates_scenarios(tmp_path):
    store = TeamProfileStore(tmp_path/"team.sqlite")
    store.add_game(observations())
    old = store.profile("CHI",NOW)
    second = store.profile("SEA",NOW)
    packet = matchup_packet(old,second,NOW)
    bound = bind_matchup(snapshot(),packet)
    new = attach_coaching(old,context())
    assert new["version"] != old["version"] and "coaching" not in old
    assert new["scheme"]["pressure_rate"]["value"] is None
    updated = bind_matchup(bound,matchup_packet(new,second,NOW))
    assert not updated.scenarios


def test_same_day_coaching_change_does_not_label_old_games_as_new_era(tmp_path):
    store = TeamProfileStore(tmp_path/"team.sqlite")
    store.add_game(observations())
    new_rows = observations("2",yards=300,plays=50)
    for row in new_rows:
        row["kickoff"] = (NOW-timedelta(days=1)).isoformat()
    store.add_game(new_rows)
    profile = attach_coaching(store.profile("CHI",NOW),context())
    era = profile["coaching_era"]["offense"]
    assert era["same_day_excluded"] == ["1"]
    assert era["games"] == 1 and era["metrics"]["yards_per_play"]["value"] == 6
    assert profile["coaching_era"]["defense"]["status"] == "ERA_BOUNDARY_UNVERIFIED"


def test_future_stale_or_wrong_team_coaching_rejected(tmp_path):
    store = TeamProfileStore(tmp_path/"team.sqlite")
    store.add_game(observations())
    profile = store.profile("CHI",NOW)
    c=context();c["directory_verified_at"]=(NOW-timedelta(days=8)).isoformat()
    with pytest.raises(ValueError,match="stale"):
        attach_coaching(profile,c)
    c=context();c["offense"]["sources"][0]["observed_at"]=(NOW+timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError,match="future"):
        attach_coaching(profile,c)
    with pytest.raises(ValueError,match="matching"):
        attach_coaching(profile,context("SEA"))


def test_caller_not_inferred_from_title_and_rates_need_scope(tmp_path):
    store=TeamProfileStore(tmp_path/"team.sqlite");store.add_game(observations())
    profile=store.profile("CHI",NOW)
    c=context()
    updated=attach_coaching(profile,c)
    matchup=coaching_matchup_context(updated,attach_coaching(store.profile("SEA",NOW),context("SEA")))
    assert matchup["defensive_play_caller"]["name"] is None
    assert matchup["automatic_probability_multiplier"] is None
    c["reported_rates"]=[dict(metric="blitz_rate",value=.7,window="",definition="",sources=c["offense"]["sources"])]
    with pytest.raises(ValueError,match="scope"):
        attach_coaching(profile,c)


def test_staff_parser_preserves_interim_and_anchor_cards():
    html='<a class="d3-o-media-object" href="/team/coaches-roster/example"><div><h5>Interim defensive coordinator</h5><h3>Example Coach</h3></div></a>'
    records=staff_cards(BeautifulSoup(html,"html.parser"),"https://club.example")
    assert records[0]["role"]=="defensive_coordinator"
    assert records[0]["title"]=="Interim defensive coordinator"
    assert records[0]["bio_reference"]=="https://club.example/team/coaches-roster/example"


def test_no_separate_defensive_coordinator_is_explicit_not_a_missing_role(tmp_path):
    store=TeamProfileStore(tmp_path/"team.sqlite");store.add_game(observations())
    c=context();c["staff"]["defensive_coordinator"]["name"]=None
    c["staff"]["defensive_coordinator"]["status"]="NOT_SEPARATELY_LISTED"
    assert attach_coaching(store.profile("CHI",NOW),c)["coaching"]["staff"]["defensive_coordinator"]["name"] is None


def test_coach_identity_normalizes_apostrophes_but_rejects_wrong_person(tmp_path):
    store=TeamProfileStore(tmp_path/"team.sqlite");store.add_game(observations())
    c=context();c["staff"]["defensive_coordinator"]["name"]="Chris O\u2019Leary"
    c["play_callers"]["defense"]["name"]="Chris O'Leary"
    c["play_callers"]["defense"]["status"]="CONFIRMED"
    assert attach_coaching(store.profile("CHI",NOW),c)
    c["play_callers"]["defense"]["name"]="Other Coach"
    with pytest.raises(ValueError,match="reconciled"):
        attach_coaching(store.profile("CHI",NOW),c)


def test_historical_tactical_foundation_cannot_be_assigned_to_wrong_coach(tmp_path):
    store=TeamProfileStore(tmp_path/"team.sqlite");store.add_game(observations())
    c=context()
    c["offense"]["foundation"]=dict(coach="HC",scope="HISTORICAL_BACKGROUND",description="Old scheme, not current usage",
                                     sources=copy.deepcopy(c["offense"]["sources"]))
    assert attach_coaching(store.profile("CHI",NOW),c)
    c["offense"]["foundation"]["coach"]="Former Coach"
    with pytest.raises(ValueError,match="unreconciled"):
        attach_coaching(store.profile("CHI",NOW),c)


def test_changed_directory_staff_requires_re_review_instead_of_old_scheme_template(tmp_path):
    import json
    from research.review_coaching_2026 import run
    directory=tmp_path/"directory.json"
    directory.write_text(json.dumps({"teams":[dict(team_id="BUF",directory_source={},staff=[
        dict(role="head_coach",name="Different Head Coach",title="Head Coach")])]}))
    evidence=tmp_path/"evidence.json";evidence.write_text("[]")
    with pytest.raises(ValueError,match="staff changed"):
        run(directory,evidence,tmp_path/"output.json")
