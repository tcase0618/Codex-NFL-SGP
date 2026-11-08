"""Validate historical input availability before allowing a pregame replay.

A gate audit is not a completed backtest. Missing evidence never becomes a
loss, a pass on betting merit, or an invented pregame snapshot.
"""

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

from .complete_parlays import load_games


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed


def validate_evidence(evidence: dict, cutoff: datetime, purpose: str) -> list[str]:
    reasons = []
    if evidence.get("purpose") != purpose:
        reasons.append(f"wrong evidence purpose: {purpose}")
    if evidence.get("uses_postgame_participation") or "didNotPlay" in json.dumps(evidence):
        reasons.append("postgame participation forbidden in selection")
    if not all(evidence.get(key) for key in ("reference", "content_sha256", "published_at", "version_available_at")):
        reasons.append(f"missing source/version provenance: {purpose}")
        return reasons
    for key in ("published_at", "version_available_at"):
        try:
            if parse_time(evidence[key]) > cutoff:
                reasons.append(f"evidence after decision cutoff: {purpose}:{key}")
        except (ValueError, TypeError):
            reasons.append(f"invalid evidence timestamp: {purpose}:{key}")
    if len(evidence["content_sha256"]) != 64 or any(character not in "0123456789abcdef" for character in evidence["content_sha256"]):
        reasons.append(f"invalid content hash: {purpose}")
    # The caller must supply an inspectable immutable archive/version record,
    # not merely the current page's original publication date.
    if not evidence.get("archive_reference"):
        reasons.append(f"missing historical version evidence: {purpose}")
    return reasons


def audit_game(game: dict, bundle: dict | None) -> dict:
    cutoff = parse_time(game["kickoff"]) - timedelta(minutes=30)
    reasons = []
    if bundle is None:
        reasons = ["missing archived pregame team membership and active/inactive roster",
                   "missing historical availability/version proof for model inputs"]
    else:
        if bundle.get("game_id") != game["id"]:
            reasons.append("game mismatch")
        for purpose in ("roster", "model_inputs"):
            reasons.extend(validate_evidence(bundle.get(purpose, {}), cutoff, purpose))
        if not bundle.get("active_players"):
            reasons.append("no explicit pregame active player universe")
    return {"game_id": game["id"], "game": game["name"], "week": game["week"],
            "kickoff": game["kickoff"], "decision_cutoff": cutoff.isoformat(),
            "status": "INPUTS_REQUIRE_MANUAL_SOURCE_VERIFICATION" if not reasons else "UNTESTABLE_MISSING_EVIDENCE",
            "reasons": reasons}


def run(output: Path, cache: Path) -> dict:
    games = [game for game in load_games(cache) if game["season"] == 2026]
    # Current cached summaries and postgame rosters deliberately do not satisfy
    # this registry. Do not silently backdate them.
    registry_path = output / "pregame-evidence-registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.exists() else {}
    rows = [audit_game(game, registry.get(game["id"])) for game in games]
    inventory_path = output / "pregame-source-inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8")) if inventory_path.exists() else {}
    source_coverage = {record["game_id"]: record for record in inventory.get("game_coverage", [])}
    for row in rows:
        row["collected_inactive_source_leads"] = source_coverage.get(row["game_id"], {}).get("sources", [])
    report = {"kind": "PREGAME_INPUT_GATE_AUDIT_NOT_A_BACKTEST", "games": len(rows),
              "decision_rule": "30 minutes before kickoff", "games_missing_evidence": sum(bool(row["reasons"]) for row in rows),
              "certified_pregame_backtest_results": None, "records": rows,
              "source_collection": inventory.get("summary"),
              "method_selection": "Rules were selected after the 2026 audit; even reconstructed inputs would not create untouched validation."}
    (output / "strict-pregame-coverage.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = ["# Strict pregame replay: evidence availability", "",
             "No clean pregame win/loss result is certified by this run. This is an input gate audit, not a completed backtest. The earlier 146–338 participation-filtered result is excluded because its player selection used postgame didNotPlay flags.", "",
             "The decision cutoff is 30 minutes before kickoff. A complete team-membership and active/inactive universe must be documented before that time, including active players who eventually never play. Every model input must have inspectable version/availability proof from before the cutoff. Today’s final boxes and roster records cannot establish those facts. The rule selection occurred after inspecting 2026 results and therefore also requires an untouched later evaluation for independent validation.", "",
             "Official sources have been collected in pregame-source-inventory.json. It records current HTML/text, publication/update metadata, hashes, and per-game inactive coverage. No complete, version-verified roster/input bundle has yet been assembled from that collection. An inactive list does not prove a player omitted from it was on that team's roster.", "",
             "Source leads: [Jets–Raiders inactives](https://www.newyorkjets.com/news/jets-vs-raiders-nfl-game-inactives-11-1-2026), [NFL Week 1 Sunday inactives](https://www.nfl.com/news/inactive-reports-sunday-week-1-2026-nfl-season), [NFL Week 8 Sunday inactives](https://www.nfl.com/news/nfl-week-8-inactives-players-ruled-out-sunday-2026-season).", "",
             f"Games checked: {len(rows)}. Games missing required evidence: {report['games_missing_evidence']}. A missing-evidence game is neither a win/loss nor a pass on wagering merit.", "",
             "| Week | Game | Decision cutoff (UTC) | Status | Missing evidence |", "|---:|---|---|---|---|"]
    for row in rows:
        lines.append(f"| {row['week']} | {row['game']} | {row['decision_cutoff']} | {row['status']} | {'; '.join(row['reasons'])} |")
    (output / "strict-pregame-coverage.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.output, args.cache)
    print(json.dumps({key: value for key, value in report.items() if key != "records"}, indent=2))
