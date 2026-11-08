"""Week-ordered reconstruction; input availability is not pregame-certified."""

import argparse
import csv
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from .calibration import fit_calibrator
from .replay_2026 import BASE, fetch_json, forecasts, parse_box, schedule


def _load_boxes(games: list[dict], cache: Path) -> dict[str, dict]:
    boxes = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        tasks = {pool.submit(fetch_json, f"{BASE}/summary?event={game['id']}", cache): game for game in games}
        for future in as_completed(tasks):
            game = tasks[future]
            boxes[game["id"]] = parse_box(game, future.result())
    return boxes


def training_rows(cache: Path) -> list[dict]:
    games = [game for week in range(1, 19) for game in schedule(2025, week, cache)]
    boxes = _load_boxes(games, cache)
    history = defaultdict(list)
    rows = []
    for game in sorted(games, key=lambda item: item["kickoff"]):
        box = boxes[game["id"]]
        if game["week"] >= 5:
            rows.extend(forecasts(box, history))
        for team in box["teams"]:
            history[team].append(box)
    return [row for row in rows if row["outcome"] is not None]


def calibrate_by_week(baseline: list[dict], train: list[dict]) -> tuple[list[dict], list[dict]]:
    by_market: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for row in train:
        by_market[row["market"]].append((row["probability"], row["outcome"]))
    calibrated_rows = []
    fit_log = []
    for week in range(1, 9):
        calibrators = {market: fit_calibrator(examples) for market, examples in by_market.items()}
        fit_log.append({"week": week, "training_examples_by_market": {market: len(examples) for market, examples in by_market.items()},
                        "chronology_scope":"week ordering only; row-level input and label availability unverified",
                        "coefficients": {market: {"intercept": model.intercept, "slope": model.slope,
                                                   "sample_size": model.sample_size} if model else None
                                         for market, model in calibrators.items()}})
        current = [row for row in baseline if int(row["week"]) == week]
        for row in current:
            model = calibrators.get(row["market"])
            row = dict(row)
            row["calibrated_probability"] = round(model.predict(float(row["probability"])), 8) if model else ""
            row["calibration_status"] = "week_ordered_reconstruction_unverified_inputs" if model else "insufficient_training"
            calibrated_rows.append(row)
        for row in current:
            if row["outcome"] != "":
                by_market[row["market"]].append((float(row["probability"]), int(row["outcome"])))
    return calibrated_rows, fit_log


def summarize_week(calibrated_rows, week):
    rows=[r for r in calibrated_rows if int(r['week'])==week and r['outcome']!='' and r['calibrated_probability']!='']
    count=len(rows)
    return {'week':week,'resolved':count,
        'raw_brier':sum((float(r['probability'])-int(r['outcome']))**2 for r in rows)/count if count else None,
        'calibrated_brier':sum((float(r['calibrated_probability'])-int(r['outcome']))**2 for r in rows)/count if count else None,
        'raw_mean':sum(float(r['probability']) for r in rows)/count if count else None,
        'calibrated_mean':sum(float(r['calibrated_probability']) for r in rows)/count if count else None,
        'hit_rate':sum(int(r['outcome']) for r in rows)/count if count else None}


def run(output: Path, cache: Path) -> dict:
    train = training_rows(cache)
    source = output / "replay-2026-predictions.csv"
    with source.open(newline="", encoding="utf-8") as handle:
        baseline = list(csv.DictReader(handle))
    calibrated_rows, fit_log = calibrate_by_week(baseline, train)
    path = output / "replay-2026-calibrated.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(calibrated_rows[0]))
        writer.writeheader()
        writer.writerows(calibrated_rows)
    weekly = [summarize_week(calibrated_rows,week) for week in range(1,9)]
    summary = {"status": "RESEARCH_ONLY", "training_2025_resolved": len(train), "season": 2026,
               "historically_pregame_certified":False,"validation_status":"RECONSTRUCTED_INPUTS_NOT_PREGAME_CERTIFIED",
               "training_window": "2025 weeks 5-18, then prior completed 2026 weeks only",
               "generated_at": datetime.now(timezone.utc).isoformat(), "weekly": weekly, "fits": fit_log}
    (output / "replay-2026-calibration-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.cache), indent=2))
