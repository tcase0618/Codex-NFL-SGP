"""Create an exclusive new context overlay, never write to the input store."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from football_engine.team_context_metrics import REVISION, SUPPORTED, VIEWS, compute_cohorts, utc
from football_engine.team_metrics import TEAM_CATALOG


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def archive_file(path, expected, folder):
    path = Path(path)
    actual = digest(path)
    if actual != expected:
        raise ValueError(f"Source hash mismatch: {path}")
    target = folder / (actual + "".join(path.suffixes))
    if not target.exists():
        shutil.copyfile(path, target)
    if digest(target) != expected:
        raise ValueError(f"Archived source hash mismatch: {target}")
    return str(target.resolve())


def reviewed_context(path, raw_dir, as_of):
    """Retain verified current chart evidence, with no availability inference."""
    if path is None:
        return [], [], {"status": "NO_REVIEWED_CONTEXT"}
    raw = path.read_bytes()
    target = raw_dir / (hashlib.sha256(raw).hexdigest() + ".json")
    target.write_bytes(raw)
    artifact = json.loads(raw)
    sources, rows, errors = [], [], []
    for source in artifact["sources"]:
        if source["kind"] not in ("roster", "depth"):
            continue
        sid = "context:official:" + source["content_sha256"]
        record = {"id": sid, "team": source["team"], "kind": source["kind"],
                  "reference": source["reference"], "observed_at": source["retrieved_at"],
                  "sha256": source["content_sha256"], "historical_pregame_eligible": False}
        if utc(source["retrieved_at"]) > utc(as_of):
            errors.append({"id": sid, "reason": "SOURCE_AFTER_CUTOFF"})
            continue
        try:
            record["path"] = archive_file(source["html_file"], record["sha256"], raw_dir)
            images = []
            for image in source.get("image_sources", []):
                images.append({**image, "file": archive_file(image["file"], image["content_sha256"], raw_dir)})
            record["image_sources"] = images
            if source.get("status") == "VISUALLY_TRANSCRIBED":
                transcription = source.get("transcription", {})
                if transcription.get("content_sha256") not in {i["content_sha256"] for i in images}:
                    raise ValueError("Visual transcription is not bound to a verified archived image")
                record["transcription"] = transcription
            sources.append(record)
            for row in source.get("rows", []):
                rows.append({"team": source["team"], "kind": source["kind"], "source_id": sid,
                             "observed_at": source["retrieved_at"], "data": row,
                             "historical_version_verified": False})
        except (OSError, ValueError) as exc:
            errors.append({"id": sid, "reason": str(exc)})
    return sources, rows, {"rows": len(rows), "sources": len(sources), "quarantined": errors,
                          "status": "CURRENT_CAPTURE_ONLY_NOT_AVAILABILITY_OR_CONTINUITY_PROOF"}


def build(base, output, as_of, seasons, views=VIEWS, context=None, compare_base=False):
    base, output = Path(base).resolve(), Path(output).resolve()
    utc(as_of)
    if not set(views) <= set(VIEWS):
        raise ValueError("Unsupported view")
    # Exclusive directory creation guards against mutating an earlier derived run.
    output.mkdir(parents=True, exist_ok=False)
    raw_dir = output / "raw"
    raw_dir.mkdir()
    before_hash = digest(base)
    db = sqlite3.connect(base.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    base_sources = {r["id"]: dict(r) for r in db.execute("SELECT * FROM sources")}
    eligible = {k for k, s in base_sources.items() if utc(s["observed_at"]) <= utc(as_of)}
    all_results, used, teams_by_season = [], set(), {}
    cohort_totals = {}
    for season in seasons:
        games = [{**dict(r), "data": json.loads(r["data"])} for r in db.execute(
            "SELECT * FROM games WHERE season=? AND game_date < ?", (season, utc(as_of).date().isoformat()))
                 if r["source_id"] in eligible]
        ids = {g["id"] for g in games}
        plays = [{**json.loads(r["data"]), "game_id": r["game_id"], "play_id": r["play_id"],
                  "posteam": r["posteam"], "defteam": r["defteam"], "source_id": r["source_id"]}
                 for r in db.execute("SELECT * FROM plays WHERE season=?", (season,))
                 if r["game_id"] in ids and r["source_id"] in eligible and r["posteam"] and r["defteam"]]
        teams = sorted({g[k] for g in games for k in ("home", "away")})
        teams_by_season[season] = teams
        cohort_totals[season] = {"games": len(games), "plays": len(plays)}
        for team in teams:
            for view in views:
                values = compute_cohorts(games, plays, team, view)
                for metric_id, side in SUPPORTED.items():
                    result = {**next(m for m in TEAM_CATALOG if m["id"] == metric_id), **values[side],
                              "team": team, "season": season, "view": view, "as_of": as_of,
                              "snapshot_as_of": as_of, "definition_revision": REVISION,
                              "source_vintage": "CURRENT_ARCHIVE_RETROSPECTIVE_NOT_PREGAME_CERTIFIED",
                              "provider_model_dependency": "nflverse EPA model; training vintage not independently verified",
                              "game_cutoff": "game_date strictly before UTC cutoff date",
                              "requirement": "archived_play_by_play_descriptive_derivation",
                              "definition_status": "documented_descriptive_formula"}
                    used.update(result["source_ids"])
                    result["source_ids"] = ["context:base:" + str(i) for i in result["source_ids"]]
                    result["feature_signature"] = f"{REVISION}|{side}|{view}"
                    all_results.append(result)
        print(json.dumps({"season": season, **cohort_totals[season], "teams": len(teams)}), flush=True)
    sources = []
    for sid in sorted(used):
        source = base_sources[sid]
        sources.append({**source, "id": "context:base:" + str(sid), "base_source_id": sid,
                        "path": archive_file(source["path"], source["sha256"], raw_dir)})
    current_sources, context_rows, context_report = reviewed_context(context, raw_dir, as_of)
    sources.extend(current_sources)
    sources = list({s["id"]: s for s in sources}.values())
    # All 401 labels remain defined by the parent catalog. Only five exact IDs
    # are emitted; the remaining unsupported IDs return None for parent fallback.
    report = {"revision": REVISION, "as_of": as_of, "input_database": str(base), "input_sha256": before_hash,
              "cohorts": cohort_totals, "views": list(views), "supported_ids": list(SUPPORTED),
              "overlay_rows": len(all_results), "populated": sum(r["value"] is not None for r in all_results),
              "qualified": sum(r["qualified"] for r in all_results), "reviewed_context": context_report,
              "personnel_scalar_coverage": {"before": 0, "after": 0, "slots_per_team": 20,
                                            "reason": "No historical lineup/availability/validated unit rating proof"},
              "rates": {}, "models_calibrated": False, "historical_pregame_eligible": False}
    if compare_base:
        from football_engine.team_quant import TeamQuantStore
        store = TeamQuantStore(base)
        base_code = Path(__file__).parents[1] / "football_engine/team_quant.py"
        report["comparison_calculation_sha256"] = digest(base_code)
        overlay = {(r["team"], r["season"], r["id"]): r for r in all_results if r["view"] == "season"}
        for season, teams in teams_by_season.items():
            before = after = qualified_before = qualified_after = 0
            additions = Counter()
            for team in teams:
                for metric in TEAM_CATALOG:
                    old = store.value(team, season, metric["id"], "season", as_of)
                    new = overlay.get((team, season, metric["id"])) or old
                    before += old["value"] is not None
                    after += new["value"] is not None
                    qualified_before += bool(old.get("qualified"))
                    qualified_after += bool(new.get("qualified"))
                    if old["value"] is None and new["value"] is not None:
                        additions[metric["id"]] += 1
            denominator = len(teams) * len(TEAM_CATALOG)
            report["rates"][season] = {"view": "season", "denominator": denominator,
                "before": before, "after": after, "before_rate": before / denominator if denominator else None,
                "after_rate": after / denominator if denominator else None,
                "qualified_before": qualified_before, "qualified_after": qualified_after,
                "additions_by_exact_id": dict(additions)}
    db.close()
    after_hash = digest(base)
    if before_hash != after_hash:
        raise RuntimeError("Input database changed during build; do not trust this overlay")
    report["input_sha256_after"] = after_hash
    report["input_unchanged"] = True
    definitions = {r["id"]: {k: r[k] for k in ("id", "label", "formula", "grain", "baseline_policy", "direction", "unit")}
                   for r in all_results}
    out = sqlite3.connect(output / "team-context.sqlite")
    out.executescript("""CREATE TABLE metrics(team TEXT,season INTEGER,view TEXT,metric_id TEXT,data TEXT,
        PRIMARY KEY(team,season,view,metric_id));
        CREATE TABLE sources(id TEXT PRIMARY KEY,data TEXT);
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE current_personnel(team TEXT,kind TEXT,source_id TEXT,data TEXT);
        CREATE TABLE definitions(metric_id TEXT PRIMARY KEY,data TEXT);""")
    out.executemany("INSERT INTO metrics VALUES(?,?,?,?,?)", [(r["team"],r["season"],r["view"],r["id"],json.dumps(r,allow_nan=False)) for r in all_results])
    out.executemany("INSERT INTO sources VALUES(?,?)", [(s["id"],json.dumps(s)) for s in sources])
    out.executemany("INSERT INTO metadata VALUES(?,?)", [(k,json.dumps(v)) for k,v in report.items()])
    out.executemany("INSERT INTO current_personnel VALUES(?,?,?,?)", [(r["team"],r["kind"],r["source_id"],json.dumps(r)) for r in context_rows])
    out.executemany("INSERT INTO definitions VALUES(?,?)", [(k,json.dumps(v)) for k,v in definitions.items()])
    out.commit()
    out.close()
    (output / "coverage.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output / "source-manifest.json").write_text(json.dumps(sources, indent=2), encoding="utf-8")
    (output / "definitions.json").write_text(json.dumps(definitions, indent=2), encoding="utf-8")
    (output / "personnel-limitations.json").write_text(json.dumps({m["id"]: {"label": m["label"], "value": None,
        "missing": True, "reason": "Historical assignment/availability or validated rating source absent; current depth rank is not evidence"}
        for m in TEAM_CATALOG if m["category"] == "personnel"}, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "populated": report["populated"], "rates": report["rates"]}), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--seasons", type=int, nargs="+", required=True)
    parser.add_argument("--views", nargs="+", default=list(VIEWS))
    parser.add_argument("--reviewed-context", type=Path)
    parser.add_argument("--compare-base", action="store_true")
    args = parser.parse_args()
    build(args.base, args.output, args.as_of, args.seasons, args.views, args.reviewed_context, args.compare_base)
