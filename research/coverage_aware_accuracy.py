"""Audit reconstructed forecast accuracy without treating missing labels as losses.

This is a retrospective sensitivity analysis. It cannot certify pregame source
versions, player availability, a combined SGP quote, or predictive skill.
"""

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from math import isfinite
from pathlib import Path


KEY = ("season", "week", "game_id", "team_id", "player_id", "market", "line")


def _probability(value, field):
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"invalid {field}") from exc
    if not isfinite(result) or not 0 <= result <= 1:
        raise ValueError(f"invalid {field}")
    return result


def _group(rows):
    count = len(rows)
    resolved = [row for row in rows if row["outcome"] is not None]
    missing = count - len(resolved)
    scores = {}
    for field in ("raw", "calibrated"):
        known = sum((row[field] - row["outcome"]) ** 2 for row in resolved)
        unknown = [row[field] for row in rows if row["outcome"] is None]
        scores[field] = {
            "resolved_brier": known / len(resolved) if resolved else None,
            "all_row_lower_bound": (known + sum(min(p * p, (1 - p) ** 2) for p in unknown)) / count,
            "all_row_upper_bound": (known + sum(max(p * p, (1 - p) ** 2) for p in unknown)) / count,
        }
    # Use the same unknown binary outcome for both models in each row. This
    # gives tighter paired bounds than subtracting two independent intervals.
    known_delta = sum((row["calibrated"] - row["outcome"]) ** 2
                      - (row["raw"] - row["outcome"]) ** 2 for row in resolved)
    unknown_deltas = [((row["calibrated"] ** 2 - row["raw"] ** 2),
                       ((1 - row["calibrated"]) ** 2 - (1 - row["raw"]) ** 2))
                      for row in rows if row["outcome"] is None]
    lower = (known_delta + sum(min(pair) for pair in unknown_deltas)) / count
    upper = (known_delta + sum(max(pair) for pair in unknown_deltas)) / count
    return {"rows": count, "resolved": len(resolved), "unresolved": missing,
            "resolved_fraction": len(resolved) / count,
            "observed_hit_rate": sum(row["outcome"] for row in resolved) / len(resolved) if resolved else None,
            "scores": scores, "paired_calibrated_minus_raw_brier_bounds": [lower, upper],
            "comparison": "CALIBRATED_BETTER_FOR_ALL_MISSING_LABELS" if upper < 0 else
                          "RAW_BETTER_FOR_ALL_MISSING_LABELS" if lower > 0 else "INDETERMINATE"}


def _verify_baseline(path: Path, rows_by_key: dict) -> dict:
    raw = Path(path).read_bytes()
    seen = set()
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = tuple(reader.fieldnames or ())
        if not set(KEY + ("probability", "outcome")) <= set(fields):
            raise ValueError("baseline forecast columns incomplete")
        for row in reader:
            key = tuple(row[field] for field in KEY)
            if key in seen or key not in rows_by_key:
                raise ValueError("baseline forecast grain differs from calibrated rows")
            seen.add(key)
            if any(row[field] != rows_by_key[key].get(field) for field in fields):
                raise ValueError("calibrated file changed frozen raw forecast or outcome")
    if seen != set(rows_by_key):
        raise ValueError("baseline forecast grain differs from calibrated rows")
    return {"path": str(Path(path).resolve()), "sha256": hashlib.sha256(raw).hexdigest()}


def evaluate_csv(path: Path, *, baseline: Path | None = None) -> dict:
    raw = Path(path).read_bytes()
    by_week, by_market, all_rows = defaultdict(list), defaultdict(list), []
    keys = set()
    rows_by_key = {}
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not set(KEY + ("probability", "calibrated_probability", "outcome")) <= set(reader.fieldnames or ()):
            raise ValueError("reconstructed forecast columns incomplete")
        for record in reader:
            key = tuple(record[field] for field in KEY)
            if any(not value for value in key) or key in keys:
                raise ValueError("missing or duplicate forecast grain")
            keys.add(key)
            rows_by_key[key] = record
            if record["outcome"] not in ("", "0", "1"):
                raise ValueError("invalid or ambiguous outcome")
            row = {"raw": _probability(record["probability"], "raw probability"),
                   "calibrated": _probability(record["calibrated_probability"], "calibrated probability"),
                   "outcome": int(record["outcome"]) if record["outcome"] else None}
            all_rows.append(row)
            by_week[record["week"]].append(row)
            by_market[record["market"]].append(row)
    if not all_rows:
        raise ValueError("empty forecast file")
    baseline_source = _verify_baseline(baseline, rows_by_key) if baseline is not None else None
    return {"kind": "RECONSTRUCTED_COVERAGE_AWARE_ACCURACY_NOT_PREGAME_CERTIFIED",
            "source": {"path": str(Path(path).resolve()), "sha256": hashlib.sha256(raw).hexdigest()},
            "baseline_source": baseline_source,
            "total": _group(all_rows),
            "by_week": {week: _group(rows) for week, rows in sorted(by_week.items(), key=lambda item: int(item[0]))},
            "by_market": {market: _group(rows) for market, rows in sorted(by_market.items())},
            "limits": ["Resolved-only scores condition on an observed box-score result.",
                       "Bounds include both possible binary outcomes for every unresolved row.",
                       "A true void or ineligible row would leave the denominator; binary bounds do not cover that selection change.",
                       "This reconstruction lacks certified historical input versions, game-day eligibility, offered lines and joint ticket prices."]}


def run(source: Path, output: Path, *, baseline: Path | None = None) -> dict:
    report = evaluate_csv(source, baseline=baseline)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / "coverage-aware-accuracy.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = ["# Reconstructed forecast accuracy with unresolved outcomes", "",
             "This is a sensitivity audit of retrospective, fixed-threshold research forecasts. It is not a certified pregame backtest or a parlay win/loss record.", "",
             "| Segment | Rows | Resolved | Unresolved | Resolved-only raw Brier | Resolved-only calibrated Brier | Paired all-row calibrated minus raw Brier range | Conclusion |",
             "|---|---:|---:|---:|---:|---:|---:|---|"]
    for name, item in [("All", report["total"]),
                       *((f"Week {week}", result) for week, result in report["by_week"].items()),
                       *((market, result) for market, result in report["by_market"].items())]:
        raw_score = item["scores"]["raw"]["resolved_brier"]
        cal_score = item["scores"]["calibrated"]["resolved_brier"]
        lo, hi = item["paired_calibrated_minus_raw_brier_bounds"]
        raw_label = f"{raw_score:.4f}" if raw_score is not None else "N/A"
        cal_label = f"{cal_score:.4f}" if cal_score is not None else "N/A"
        lines.append(f"| {name} | {item['rows']} | {item['resolved']} | {item['unresolved']} | "
                     f"{raw_label} | {cal_label} | [{lo:.4f}, {hi:.4f}] | {item['comparison']} |")
    lines += ["", *report["limits"], "", f"Input SHA-256: `{report['source']['sha256']}`", ""]
    (output / "coverage-aware-accuracy.md").write_text("\n".join(lines), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    print(json.dumps(run(options.source, options.output, baseline=options.baseline)["total"], indent=2))
