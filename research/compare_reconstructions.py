"""Compare frozen research selections using identical final evidence and grading."""
import argparse
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

from .complete_parlays import box_with_td, load_games, settle
from .parlay_replay import _save_once, digest
from .reconstructed_resolution import run as diagnose


def slot(ticket):
    return ticket["game_id"], ticket["type"]


def selection_key(ticket):
    return sorted((leg["player_id"], leg["team_id"], leg["market"], leg["line"]) for leg in ticket["legs"])


def paired(old_tickets, old_grades, new_tickets, new_grades):
    old = {slot(ticket): (ticket, grade) for ticket, grade in zip(old_tickets, old_grades)}
    new = {slot(ticket): (ticket, grade) for ticket, grade in zip(new_tickets, new_grades)}
    if len(old) != len(old_tickets) or len(new) != len(new_tickets) or old.keys() != new.keys():
        raise ValueError("model game/family coverage mismatch or duplicate slots")
    rows = []
    for key in sorted(old):
        a, ga = old[key]
        b, gb = new[key]
        if a["ticket_id"] != ga["ticket_id"] or b["ticket_id"] != gb["ticket_id"]:
            raise ValueError("grade/ticket alignment mismatch")
        rows.append({"game_id": key[0], "family": key[1], "week": b["week"], "game": b["game"],
                     "old_status": ga["status"], "new_status": gb["status"],
                     "selection_changed": selection_key(a) != selection_key(b),
                     "both_constructed": bool(a["legs"] and b["legs"])})
    return rows


def regrade(source, folder, final_cache, games):
    data = json.loads(source.read_text(encoding="utf-8"))
    assert digest(data["tickets"]) == data["tickets_sha256"]
    folder.mkdir()
    shutil.copytree(final_cache, folder / "final-boxes")
    _save_once(folder / "selection-manifest.json", {
        "tickets": data["tickets"], "tickets_sha256": data["tickets_sha256"],
        "original_source": str(source), "original_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "kind": "FROZEN_PRIOR_SELECTIONS_REGRADED_NOT_A_NEW_PREGAME_RUN"})
    grades = []
    for ticket in data["tickets"]:
        grade = settle(ticket, box_with_td(games[ticket["game_id"]], final_cache)) if ticket["legs"] else {
            "ticket_id": ticket["ticket_id"], "status": "blocked", "legs": []}
        grades.append({**grade, "week": ticket["week"], "type": ticket["type"], "game": ticket["game"]})
    _save_once(folder / "statistical-outcomes.json", {"tickets_sha256": data["tickets_sha256"], "grades": grades})
    diagnose(folder)
    resolved = json.loads((folder / "participation-diagnosis.json").read_text(encoding="utf-8"))
    return data["tickets"], resolved["grades"]


def run(root, output):
    output.mkdir(parents=True, exist_ok=False)
    current = root / "outputs/reconstructed-replay-20261103-v4"
    new_manifest = json.loads((current / "selection-manifest.json").read_text(encoding="utf-8"))
    new_result = json.loads((current / "participation-diagnosis.json").read_text(encoding="utf-8"))
    assert digest(new_manifest["tickets"]) == new_result["tickets_sha256"]
    games = {game["id"]: game for game in load_games(root / "work/espn-cache")}
    models = {"current": (new_manifest["tickets"], new_result["grades"])}
    for name, filename in (("previous_source", "source-informed-parlays-v4.json"),
                           ("hindsight_reference", "active-player-parlays.json")):
        models[name] = regrade(root / "outputs" / filename, output / name, current / "final-boxes", games)
    counts = lambda rows: dict(Counter(row["status"] for row in rows))
    summary = {"kind": "PAIRED_RETROSPECTIVE_COMPARISON", "valid_pregame_backtest": False, "roi": None,
               "same_final_box_archive": str(current / "final-boxes"),
               "models": {name: {"counts": counts(grades), "by_type": {
                   kind: counts([g for g in grades if g["type"] == kind]) for kind in ("3", "5", "8", "TD")}}
                   for name, (_, grades) in models.items()}, "comparisons": {}}
    for name in ("previous_source", "hindsight_reference"):
        rows = paired(*models[name], *models["current"])
        both = [row for row in rows if row["both_constructed"]]
        comparison = {"slots": len(rows), "selection_changes": sum(row["selection_changed"] for row in rows),
                      "newly_filled_slots": sum(row["old_status"] == "blocked" and row["new_status"] != "blocked" for row in rows),
                      "transitions": dict(Counter(row["old_status"] + " -> " + row["new_status"] for row in rows)),
                      "both_constructed_slots": len(both),
                      "old_counts_on_shared_constructed": dict(Counter(row["old_status"] for row in both)),
                      "new_counts_on_shared_constructed": dict(Counter(row["new_status"] for row in both)),
                      "rows": rows}
        summary["comparisons"][name] = comparison
    _save_once(output / "comparison.json", summary)
    lines = ["# Current reconstruction versus prior models", "",
             "All frozen selections are graded against the same refreshed 121 final boxes and the same settlement-only participation diagnosis. No tickets were reranked for this comparison. All results are provisional retrospective statistical targets, not certified pregame strategy performance or ROI.", "",
             "The primary baseline is the previous source-informed v4 selection artifact. The older 146–338 participation-filtered model is shown separately because its selection used postgame participation. The current run also uses repaired lagged history; this comparison cannot isolate eligibility from history repair as independent causal effects.", "",
             "| Model | Hits | Misses | Nonparticipant | Invalid | Unknown | Blocked |", "|---|---:|---:|---:|---:|---:|---:|"]
    for name, model in summary["models"].items():
        c = model["counts"]
        lines.append(f"| {name} | {c.get('won',0)} | {c.get('lost',0)} | {c.get('nonparticipant_ticket',0)} | {c.get('invalid_selection',0)} | {c.get('participation_unknown',0)} | {c.get('blocked',0)} |")
    lines += ["", "## Family comparison", "", "Values are hits / misses / nonparticipant / invalid / unknown / blocked.", "",
              "| Family | Previous source model | Current reconstruction | Hindsight reference |", "|---|---|---|---|"]
    def values(name, kind):
        c = summary["models"][name]["by_type"][kind]
        return " / ".join(str(c.get(k,0)) for k in ("won", "lost", "nonparticipant_ticket", "invalid_selection", "participation_unknown", "blocked"))
    for kind in ("3", "5", "8", "TD"):
        lines.append(f"| {kind} | {values('previous_source',kind)} | {values('current',kind)} | {values('hindsight_reference',kind)} |")
    for name, comparison in summary["comparisons"].items():
        lines += ["", f"## Paired comparison: {name}", "",
                  f"Changed selections: {comparison['selection_changes']}; newly filled slots: {comparison['newly_filled_slots']}. Both models formed {comparison['both_constructed_slots']} matching game/family slots.", "",
                  "Shared constructed slots, old categories: " + json.dumps(comparison["old_counts_on_shared_constructed"]) + ".",
                  "Shared constructed slots, current categories: " + json.dumps(comparison["new_counts_on_shared_constructed"]) + ".", "",
                  "Transitions across all requested slots: " + json.dumps(comparison["transitions"]) + "."]
    lines += ["", "Blocked slots are missing constructions, not losses. Nonparticipant tickets remain visible; no sportsbook void assumption is applied. Overlapping families are not independent trials. Historical input versions and offered quotes remain unverified; no profitability or full-engine superiority claim follows from these counts.", "",
              "## Every slot against previous source model", "", "| Week | Game | Family | Previous | Current | Selection changed |", "|---:|---|---|---|---|---|"]
    for row in summary["comparisons"]["previous_source"]["rows"]:
        lines.append(f"| {row['week']} | {row['game']} | {row['family']} | {row['old_status']} | {row['new_status']} | {row['selection_changed']} |")
    (output / "report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    _save_once(output / "sha256.json", {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()})
    return {"models": summary["models"], "comparisons": {name: {k:v for k,v in c.items() if k != "rows"} for name,c in summary["comparisons"].items()}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.output), indent=2))
