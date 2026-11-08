import json
import sqlite3

import pytest

from football_engine.player_metrics import CATALOG, VIEWS, libraries_for_position
from football_engine.player_profile_store import PlayerProfileStore, aggregate, number


def test_complete_position_catalog_and_views():
    expected={"QB":65,"RB":40,"WR":45,"TE":35,"OT":40,"OG":40,"C":40,"EDGE":35,"DI":35,"LB":40,"CB":45,"S":35,
              "K":20,"P":20,"LS":20,"KR":20,"PR":20,"FB":20}
    assert {position:len(metrics) for position,metrics in CATALOG.items()}==expected
    assert sum(map(len,CATALOG.values()))==615
    assert len(VIEWS)==18
    assert len({metric['id'] for metrics in CATALOG.values() for metric in metrics})==615


def test_ambiguous_deployment_does_not_choose_one_specialist_role():
    assert libraries_for_position('DL')==['EDGE','DI']
    assert libraries_for_position('OL')==['OT','OG','C']
    assert libraries_for_position('WR')==['WR']
    assert libraries_for_position('unconfirmed')==[]


def test_ratio_uses_actual_denominators_not_average_percentages():
    value,games,attempts=aggregate([{"completions":1,"attempts":1},{"completions":1,"attempts":9}],"completion")
    assert value==20 and games==2 and attempts==10


def test_missing_zero_denominator_and_nonfinite_never_become_facts():
    assert aggregate([{"attempts":10}],"completion")[0] is None
    assert aggregate([{"completions":0,"attempts":0}],"completion")[0] is None
    assert aggregate([{"receptions":0}],"receptions")[0]==0
    assert number("NaN") is None and number("Infinity") is None


def test_tracking_weighting_and_attribution():
    value,games,attempts=aggregate([{"avg_time_to_throw":2,"attempts":10},{"avg_time_to_throw":3,"attempts":30}],"time_throw")
    assert value==2.75 and games==2 and attempts==40


@pytest.fixture
def store(tmp_path):
    path=tmp_path/'players.sqlite';db=sqlite3.connect(path)
    db.executescript('CREATE TABLE sources(id INTEGER,reference TEXT,observed_at TEXT,sha256 TEXT); CREATE TABLE observations(player_id TEXT,season INTEGER,week INTEGER,game_type TEXT,kind TEXT,data TEXT,source_id INTEGER);')
    db.execute('INSERT INTO sources VALUES(1,?,?,?)',('fixture:stats','2026-11-03T18:00:00+00:00','a'*64))
    for year,week,attempts,completions in [(2023,1,10,5),(2024,1,10,9),(2024,2,10,7),(2024,3,10,5),(2024,4,10,3),(2026,1,10,10)]:
        db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,1)',('p',year,week,'REG','stats',json.dumps({'attempts':attempts,'completions':completions,'home_away':'home' if week%2 else 'away'})))
    # Tracking exists for week 1 only, outside the actual last-three-game window.
    db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,1)',('p',2024,1,'REG','passing',json.dumps({'attempts':10,'avg_time_to_throw':2.5})))
    db.commit();db.close();return PlayerProfileStore(path)


def metric(label):return next(m for m in CATALOG['QB'] if m['label']==label)


def test_career_respects_selected_season_and_source_knowledge_cutoff(store):
    assert store.metric('p',metric('Completion %'),2024,'career')['value']==58
    assert store.metric('p',metric('Completion %'),2024,as_of='2026-11-02T12:00:00Z')['value'] is None
    with pytest.raises(ValueError,match='timezone'):store.metric('p',metric('Completion %'),2024,as_of='2026-11-02')


def test_last_n_uses_game_windows_not_last_nonmissing_tracking_rows(store):
    assert store.metric('p',metric('Time to throw'),2024,'season')['value']==2.5
    assert store.metric('p',metric('Time to throw'),2024,'last3')['value'] is None
    assert store.metric('p',metric('Completion %'),2024,'last3')['value']==50


def test_home_away_and_matched_week_year_over_year(store):
    assert store.metric('p',metric('Completion %'),2024,'home')['value']==70
    assert store.metric('p',metric('Completion %'),2024,'away')['value']==50
    assert store.metric('p',metric('Completion %'),2024,'yoy')['value']==40


def test_unknown_charting_and_splits_stay_unavailable(store):
    assert store.metric('p',metric('Decision grade'),2024)['value'] is None
    assert store.metric('p',metric('Completion %'),2024,'blitz')['value'] is None
