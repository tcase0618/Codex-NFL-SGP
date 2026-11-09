import csv

import pytest

from research.coverage_aware_accuracy import evaluate_csv, run


FIELDS = ("season", "week", "game_id", "team_id", "player_id", "market", "line",
          "probability", "calibrated_probability", "outcome")


def write_rows(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def record(player, raw, calibrated, outcome):
    return dict(season="2026", week="1", game_id="g1", team_id="CHI", player_id=player,
                market="receiving_yards", line="40", probability=raw,
                calibrated_probability=calibrated, outcome=outcome)


def test_unknown_outcome_bounds_use_the_same_label_for_both_models(tmp_path):
    path = tmp_path / "forecast.csv"
    write_rows(path, [record("one", .8, .6, "0"), record("two", .8, .6, "")])
    result = evaluate_csv(path)["total"]
    assert result["rows"] == 2 and result["resolved"] == 1 and result["unresolved"] == 1
    assert result["scores"]["raw"]["resolved_brier"] == pytest.approx(.64)
    assert result["scores"]["calibrated"]["resolved_brier"] == pytest.approx(.36)
    # Known delta = -.28. Unknown delta is -.28 if 0 or +.12 if 1.
    assert result["paired_calibrated_minus_raw_brier_bounds"] == pytest.approx([-.28, -.08])
    assert result["comparison"] == "CALIBRATED_BETTER_FOR_ALL_MISSING_LABELS"
    report = run(path, tmp_path / "report")
    assert (tmp_path / "report" / "coverage-aware-accuracy.md").is_file()
    assert report["source"]["sha256"]
    with pytest.raises(FileExistsError):
        run(path, tmp_path / "report")


def test_duplicate_grain_and_invalid_probability_fail_closed(tmp_path):
    path = tmp_path / "forecast.csv"
    row = record("one", .8, .6, "1")
    write_rows(path, [row, row])
    with pytest.raises(ValueError, match="duplicate forecast grain"):
        evaluate_csv(path)
    write_rows(path, [record("one", .8, "nan", "1")])
    with pytest.raises(ValueError, match="invalid calibrated probability"):
        evaluate_csv(path)


def test_all_unresolved_rows_do_not_produce_an_observed_hit_rate(tmp_path):
    path = tmp_path / "forecast.csv"
    write_rows(path, [record("one", .5, .6, "")])
    report = run(path, tmp_path / "report")
    assert report["total"]["resolved"] == 0
    assert report["total"]["observed_hit_rate"] is None
    assert "N/A" in (tmp_path / "report" / "coverage-aware-accuracy.md").read_text()


def test_calibrated_rows_must_retain_exact_raw_forecast_and_outcome(tmp_path):
    calibrated = tmp_path / "calibrated.csv"
    baseline = tmp_path / "baseline.csv"
    row = record("one", .8, .6, "0")
    write_rows(calibrated, [row])
    with baseline.open("w", newline="", encoding="utf-8") as handle:
        fields = tuple(field for field in FIELDS if field != "calibrated_probability")
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow({key: row[key] for key in fields})
    assert evaluate_csv(calibrated, baseline=baseline)["baseline_source"]["sha256"]
    changed = dict(row, probability=.7)
    write_rows(calibrated, [changed])
    with pytest.raises(ValueError, match="changed frozen raw forecast"):
        evaluate_csv(calibrated, baseline=baseline)
