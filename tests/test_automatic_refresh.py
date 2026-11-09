import copy
import json
from datetime import datetime, timedelta

import pytest

from research.automatic_refresh import event_game, game_stats_hash, known_game_hashes, staff_matches, single_run, run, near_game
from football_engine.team_profiles import TeamProfileStore
from test_engine import NOW
from test_team_profiles import observations


def event(completed=True, phase=2):
    return dict(id="1",name="Fixture game",season=dict(year=2026,type=phase),week=dict(number=1),
                date=(NOW-timedelta(days=2)).isoformat(),competitions=[dict(status=dict(type=dict(completed=completed)))])


def test_only_completed_regular_or_postseason_events_are_candidates():
    assert event_game(event(False)) is None
    assert event_game(event(phase=1)) is None
    assert event_game(event())["week"]==1
    assert event_game(event(phase=3))["season_type"]==3


def test_upcoming_identity_is_schedule_scoped_and_never_final_proof():
    from research.automatic_refresh import upcoming_game_context
    item=event(completed=False)
    item["date"]=(NOW+timedelta(days=1)).isoformat()
    item["competitions"][0]["competitors"]=[{"team":{"abbreviation":"CHI"}}, {"team":{"abbreviation":"SEA"}}]
    source={"reference":"fixture:schedule","observed_at":NOW.isoformat()}
    contexts=upcoming_game_context(item,source,NOW)
    assert {c["team_id"] for c in contexts}=={"CHI","SEA"}
    assert all(c["game_id"]=="1" and c["week"]==1 and c["schedule_source"]==source for c in contexts)
    item["competitions"][0]["status"]["type"]["completed"]=True
    assert upcoming_game_context(item,source,NOW)==[]


def test_source_context_refreshes_around_kickoff_without_waiting_for_a_final():
    e=event();e["date"]=NOW.isoformat()
    assert near_game(e,NOW-timedelta(minutes=90))
    assert near_game(e,NOW+timedelta(hours=4))
    assert not near_game(e,NOW-timedelta(hours=3))
    assert not near_game(e,NOW+timedelta(hours=6))


def test_metadata_and_equivalent_timestamp_do_not_count_as_statistical_corrections():
    rows=observations()
    changed=copy.deepcopy(rows)
    for row in changed:
        row["source"]["sha256"]="different raw metadata"
        row["available_at"]=(NOW+timedelta(hours=1)).isoformat()
        row["kickoff"]=row["kickoff"].replace("+00:00","Z")
    assert game_stats_hash(rows)==game_stats_hash(changed)
    changed[0]["offense"]["total_yards"]+=1
    assert game_stats_hash(rows)!=game_stats_hash(changed)


def test_future_observation_cannot_mask_current_missing_game(tmp_path):
    store=TeamProfileStore(tmp_path/"test.sqlite")
    store.add_game(observations(available=NOW+timedelta(hours=1)))
    assert known_game_hashes(store,NOW)=={}


def test_staff_changes_and_explicit_no_dc_are_distinguished():
    reviewed=dict(staff={"head_coach":dict(name="Coach",title="Head Coach"),
                         "defensive_coordinator":dict(name=None,title=None)})
    current=dict(staff=[dict(role="head_coach",name="Coach",title="Head Coach")])
    assert staff_matches(current,reviewed)
    current["staff"][0]["name"]="Replacement"
    assert not staff_matches(current,reviewed)


def test_refreshes_cannot_overlap_and_lock_releases_after_failure(tmp_path):
    path=tmp_path/"run.lock"
    with single_run(path):
        with pytest.raises(OSError):
            with single_run(path):
                pass
    with single_run(path):
        pass


def test_pipeline_keeps_pending_reviews_and_source_gaps_on_quiet_poll(tmp_path,monkeypatch):
    import research.automatic_refresh as auto
    from test_coaching import context
    clock_offset = [timedelta()]
    class FixtureDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = NOW + clock_offset[0]
            return instant.astimezone(tz) if tz is not None else instant.replace(tzinfo=None)
    monkeypatch.setattr(auto, "datetime", FixtureDatetime)
    root=tmp_path/"runtime";out=tmp_path/"out";root.mkdir();out.mkdir()
    db=tmp_path/"team.sqlite"
    seed=tmp_path/"seed.json";seed.write_text(json.dumps(dict(profiles={})))
    coaches=tmp_path/"coaching.json";coaches.write_text(json.dumps(dict(teams={team:context(team) for team in ("CHI","SEA")})))
    notes=tmp_path/"notes.json";notes.write_text('{"notes":[]}')
    depth=tmp_path/"depth.json";depth.write_text('[]')
    monkeypatch.setattr(auto,"DOMAINS",dict(CHI="fixture-chi",SEA="fixture-sea"))
    fixture_event=event()
    def fetched(url,archive):
        return (dict(events=[fixture_event]) if "summary" not in url else {}),dict(reference="fixture:http",sha256="raw",observed_at=NOW.isoformat())
    monkeypatch.setattr(auto,"fetch_json",fetched)
    def parsed(game,raw,source,available):
        rows=observations()
        for row in rows:
            row["offense"]["points"]=10;row["opponent_offense"]["points"]=10
            row["source"]=source;row["available_at"]=available.isoformat()
        return rows
    monkeypatch.setattr(auto,"game_observations",parsed)
    def page(task,archive):
        team,kind,url=task
        if kind=='injury':
            import hashlib
            raw=b'<select><option selected value="/team/injury-report/week/REG-8">Week 8</option></select><table><tr><th>Player</th><th>Injury</th></tr><tr><td><a href="/team/players-roster/fixture/">Fixture Player</a></td><td>Knee</td></tr></table>'
            path=archive/(team+'-injury.html');path.write_bytes(raw)
            return dict(team=team,kind=kind,reference=url,resolved_reference=url,retrieved_at=NOW.isoformat(),
                content_sha256=hashlib.sha256(raw).hexdigest(),html_file=str(path),status='parsed',rows=[])
        return dict(team=team,kind=kind,reference=url,retrieved_at=NOW.isoformat(),content_sha256="page",status="parsed",rows=[])
    monkeypatch.setattr(auto,"fetch_page",page)
    def directory(task,archive):
        team,_=task;c=context(team)
        return dict(team_id=team,staff=[dict(role=role,name=record["name"],title=record["title"]) for role,record in c["staff"].items()],
                    directory_source=dict(reference="fixture:directory",sha256="dir",observed_at=NOW.isoformat()))
    monkeypatch.setattr(auto,"collect_team",directory)
    monkeypatch.setattr(auto,"collect_evidence",lambda u,a:dict(reference=u,error="unavailable"))
    monkeypatch.setattr(auto,"collect_news",lambda t,a:dict(source=dict(reference="fixture:news"),items=[dict(team_id=t,reference="fixture:news:"+t,headline="New reviewed source needed",status="PENDING_SOURCE_REVIEW")]))
    config=dict(runtime=str(root),output=str(out),database=str(db),seed_profiles=str(seed),coaching=str(coaches),scheme_notes=str(notes),reviewed_depth=str(depth))
    first=run(config,True)
    assert first["teams"]==2 and first["new_final_games"]==["1"]
    assert len(first["source_gaps"])==2
    clock_offset[0] = timedelta(seconds=1)
    second=run(config)
    assert not second["profiles_rebuilt"] and not second["statistical_corrections"]
    assert second["source_gaps"]==first["source_gaps"]
    state=json.loads((root/"state.json").read_text())
    assert len(state["pending_news_reviews"])==2
    assert (out/"football-profiles-latest.md").exists()
    store=TeamProfileStore(db)
    assert store.connection.execute("SELECT COUNT(*) FROM team_observations").fetchone()[0]==2
