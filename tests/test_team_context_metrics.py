import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from football_engine.team_context_metrics import TeamContextMetrics, compute_cohorts, deep_pass, utc
from football_engine.team_metrics import TEAM_CATALOG
from research.build_team_context_metrics import archive_file, build, reviewed_context


def game(gid, home, away, date="2026-10-01", **data):
    return dict(id=gid, home=home, away=away, week=3, game_date=date, data=data, source_id=1)


def play(gid, offense, defense, epa, **data):
    return dict(game_id=gid, play_id="1", posteam=offense, defteam=defense,
                epa=epa, play_type="pass", pass_attempt=1, air_yards=25, source_id=2, **data)


@pytest.fixture
def sample():
    games = [game("ab", "A", "B"), game("bc", "B", "C"), game("cd", "C", "D")]
    plays = [play("ab", "A", "B", 2), play("ab", "B", "A", 3),
             play("bc", "B", "C", 4), play("bc", "C", "B", 1),
             play("cd", "C", "D", 0), play("cd", "D", "C", -1)]
    return games, plays


def test_exact_catalog_ids_and_descriptive_direction(sample):
    from football_engine.team_context_metrics import SUPPORTED
    assert set(SUPPORTED) <= {m["id"] for m in TEAM_CATALOG}
    results = compute_cohorts(*sample, "A")
    assert results["offense"]["value"] == 2
    assert results["defense"]["value"] == 0
    assert results["deep"]["value"] == 3
    assert results["defense"]["direction"] == "lower"
    assert not results["offense"]["models_calibrated"]
    assert not results["offense"]["qualified"]


def test_all_focal_rematches_excluded_from_baseline(sample):
    games, plays = sample
    games.append(game("ba", "B", "A"))
    plays.append(play("ba", "A", "B", 999))
    result = compute_cohorts(games, plays, "A", "home")["offense"]
    assert result["value"] == 2
    assert result["baseline_counts"]["B"] == {"plays": 1, "games": 1}
    assert result["league_baseline_plays"] == 4


def test_missing_opponent_baseline_never_zero(sample):
    games, plays = sample
    result = compute_cohorts(games[:1], plays[:2], "A")["offense"]
    assert result["value"] is None and result["missing"]
    assert result["missing_reason"] in ("LEAGUE_BASELINE_MISSING", "OPPONENT_BASELINE_MISSING")


def test_zero_epa_is_valid_and_empty_cohort_is_not_zero(sample):
    games, plays = sample
    plays[1]["epa"] = 0
    assert compute_cohorts(games, plays, "A")["deep"]["value"] == 0
    assert compute_cohorts(games, plays, "A", "away")["deep"]["value"] is None


@pytest.mark.parametrize("change", [{"air_yards": None}, {"air_yards": 19}, {"sack": 1},
                                  {"qb_scramble": 1}, {"qb_spike": 1}, {"two_point_attempt": 1}])
def test_deep_pass_requires_real_depth_and_throw(change):
    p = play("ab", "A", "B", 1)
    p.update(change)
    assert not deep_pass(p)


def test_defense_state_uses_focal_team_perspective(sample):
    games, plays = sample
    plays[1]["score_differential"] = -7
    assert compute_cohorts(games, plays, "A", "leading")["deep"]["value"] == 3
    assert compute_cohorts(games, plays, "A", "trailing")["deep"]["value"] is None


def test_missing_epa_withholds_incomplete_cohort(sample):
    games, plays = sample
    plays.append(play("ab", "B", "A", None))
    result = compute_cohorts(games, plays, "A")["deep"]
    assert result["value"] is None
    assert result["missing_reason"] == "INCOMPLETE_EPA_COHORT"
    assert result["missing_epa_plays"] == 1


def test_timezone_required():
    with pytest.raises(ValueError, match="timezone"):
        utc("2026-11-04")


def test_hash_corruption_is_rejected(tmp_path):
    p = tmp_path / "raw.json"
    p.write_bytes(b"original")
    with pytest.raises(ValueError, match="hash mismatch"):
        archive_file(p, "0" * 64, tmp_path)


@pytest.fixture
def overlay(tmp_path, sample):
    games, plays = sample
    raw = tmp_path / "input.json"
    raw.write_bytes(b"archived fixture")
    sha = hashlib.sha256(raw.read_bytes()).hexdigest()
    base = tmp_path / "base.sqlite"
    db = sqlite3.connect(base)
    db.executescript("""CREATE TABLE sources(id INTEGER,reference TEXT,sha256 TEXT,observed_at TEXT,path TEXT,kind TEXT);
        CREATE TABLE games(id TEXT,season INTEGER,week INTEGER,game_date TEXT,home TEXT,away TEXT,data TEXT,source_id INTEGER);
        CREATE TABLE plays(game_id TEXT,play_id TEXT,season INTEGER,posteam TEXT,defteam TEXT,source_id INTEGER,data TEXT);""")
    for sid in (1, 2):
        db.execute("INSERT INTO sources VALUES(?,?,?,?,?,?)", (sid, "fixture", sha, "2026-10-02T00:00:00Z", str(raw), "pbp"))
    for g in games:
        db.execute("INSERT INTO games VALUES(?,?,?,?,?,?,?,?)", (g["id"],2026,g["week"],g["game_date"],g["home"],g["away"],json.dumps(g["data"]),1))
    # Cutoff-date game must never leak into the build.
    db.execute("INSERT INTO games VALUES('future',2026,4,'2026-11-04','A','B','{}',1)")
    for i, p in enumerate(plays + [play("future", "B", "A", 999)]):
        db.execute("INSERT INTO plays VALUES(?,?,?,?,?,?,?)", (p["game_id"],str(i),2026,p["posteam"],p["defteam"],2,json.dumps(p)))
    db.commit()
    db.close()
    output = tmp_path / "new"
    report = build(base, output, "2026-11-04T00:00:00Z", [2026], ["season"])
    return base, output, report


def test_builder_snapshot_bounds_namespaced_provenance_and_thread_safety(overlay):
    base, output, report = overlay
    adapter = TeamContextMetrics(output / "team-context.sqlite")
    metric = "matchup.defense_vs_deep_passing"
    assert report["input_unchanged"] and adapter.verify_base(base)
    assert report["cohorts"][2026]["games"] == 3
    assert all(s["id"].startswith("context:") for s in adapter.sources)
    with ThreadPoolExecutor(max_workers=5) as workers:
        values = list(workers.map(lambda _: adapter.value("A",2026,metric)["value"],range(20)))
    assert values == [3] * 20
    assert adapter.value("A",2026,metric,as_of="2026-10-30T00:00:00Z")["value"] is None
    assert adapter.value("A",2026,metric,as_of="2026-11-05T00:00:00Z")["snapshot_as_of"] == "2026-11-04T00:00:00Z"
    assert adapter.value("A",2026,"personnel.qb_availability") is None
    assert adapter.value("A",2026,metric,"man") is None
    assert adapter.value("A",2025,metric) is None
    with pytest.raises(sqlite3.OperationalError):
        with adapter.connect() as db:
            db.execute("DELETE FROM metrics")
    with pytest.raises(FileExistsError):
        build(base, output, "2026-11-04T00:00:00Z", [2026])
    adapter.close()


def test_verify_base_rejects_wrong_snapshot(overlay, tmp_path):
    _, output, _ = overlay
    wrong = tmp_path / "wrong.sqlite"
    wrong.write_bytes(b"wrong")
    with pytest.raises(ValueError, match="does not match"):
        TeamContextMetrics(output / "team-context.sqlite").verify_base(wrong)


def test_current_depth_never_proves_historical_availability(tmp_path):
    raw = tmp_path / "chart.html"
    raw.write_bytes(b"chart")
    target = tmp_path / "archive"
    target.mkdir()
    p = tmp_path / "context.json"
    p.write_text(json.dumps({"sources": [{"kind":"depth","team":"A","content_sha256":hashlib.sha256(raw.read_bytes()).hexdigest(),
        "reference":"official", "retrieved_at":"2026-11-03T00:00:00Z", "html_file":str(raw),
        "status":"parsed", "rows":[{"player":"Real Name", "position":"QB", "depth":1}]}]}))
    sources, rows, report = reviewed_context(p,target,"2026-11-04T00:00:00Z")
    assert len(sources) == len(rows) == 1
    assert not rows[0]["historical_version_verified"]
    assert "NOT_AVAILABILITY" in report["status"]
    assert reviewed_context(p,target,"2026-11-02T00:00:00Z")[1] == []


def test_personnel_supplemental_counts_are_not_available_starters(overlay):
    _, output, _ = overlay
    with sqlite3.connect(output / "team-context.sqlite") as db:
        for kind, data in [("roster", {"player":"Name", "position":"QB", "section":"Reserve/Injured"}),
                           ("depth", {"player":"Name", "position":"QB", "depth":1})]:
            row = {"team":"A", "kind":kind,"source_id":"context:official:test", "data":data,
                   "observed_at":"2026-11-03T00:00:00Z"}
            db.execute("INSERT INTO current_personnel VALUES(?,?,?,?)",("A",kind,row["source_id"],json.dumps(row)))
    adapter = TeamContextMetrics(output / "team-context.sqlite")
    result = adapter.personnel("A",2026)
    assert result["roster_listed_count"] == result["depth_listed_count"] == 1
    assert result["depth_row_count"] == result["depth_rank_one_listed_count"] == 1
    assert result["roster_listed_by_position"] == {"QB":1}
    assert not result["availability_inferred"]
    assert not result["historical_continuity_inferred"]
    assert result["advanced_personnel_populated"] == 0
    assert result["advanced_personnel_defined"] == 20
    assert adapter.personnel("A",2025) is None
    assert adapter.personnel("A",2026,"2026-11-02T00:00:00Z")["roster_listed_count"] is None
