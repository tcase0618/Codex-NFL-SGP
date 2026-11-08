"""Inspect loss concentrations and selection leakage in saved reconstructions."""

import argparse
import json
from collections import Counter
from pathlib import Path

from .complete_parlays import box_with_td, cached_json, load_games, BASE
from .parlay_replay import digest


def run(output: Path, cache: Path):
    artifact = json.loads((output / "active-player-parlays.json").read_text(encoding="utf-8"))
    original = json.loads((output / "all-games-parlay-manifest.json").read_text(encoding="utf-8"))
    tickets, grades = artifact["tickets"], artifact["settlements"]
    assert digest(tickets) == artifact["tickets_sha256"]
    assert digest(grades) == artifact["settlements_sha256"]
    games = load_games(cache)
    boxes = {game["id"]: box_with_td(game, cache) for game in games}
    by_old = {(ticket["game_id"], ticket["type"]): ticket for ticket in original["tickets"]}
    changed = 0
    changed_winners = 0
    bad_history = []
    unique = {}
    failure_counts = Counter()
    by_kind = {}
    for kind in ("3", "5", "8", "TD"):
        subset = [(ticket, grade) for ticket, grade in zip(tickets, grades) if ticket["type"] == kind]
        lost = [grade for _, grade in subset if grade["status"] == "lost"]
        by_kind[kind] = {"won": len(subset) - len(lost), "lost": len(lost),
                         "one_failed_leg": sum(sum(leg["status"] == "lost" for leg in grade["legs"]) == 1 for grade in lost),
                         "failed_legs": sum(leg["status"] == "lost" for grade in lost for leg in grade["legs"])}
    for ticket, grade in zip(tickets, grades):
        assert grade["ticket_sha256"] == digest(ticket)
        old = by_old[(ticket["game_id"], ticket["type"])]
        key = lambda leg: (leg["team_id"], leg["player_id"], leg["market"], leg["line"])
        if [key(leg) for leg in ticket["legs"]] != [key(leg) for leg in old["legs"]]:
            changed += 1
            changed_winners += grade["status"] == "won"
        for position, leg in enumerate(grade["legs"], start=1):
            for game_id in leg["prior_game_ids"]:
                if boxes[game_id]["kickoff"] >= ticket["kickoff"]:
                    bad_history.append((ticket["ticket_id"], game_id))
            row = {**leg, "week": ticket["week"], "game_id": ticket["game_id"], "game": ticket["game"],
                   "ticket_type": ticket["type"], "position": position}
            unique[(ticket["game_id"], *key(leg))] = row
            if leg["status"] == "lost":
                failure_counts[(leg["player"], leg["market"])] += 1
    market_summary = {}
    for market in ("passing_yards", "rushing_yards", "receiving_yards", "anytime_td"):
        subset = [leg for leg in unique.values() if leg["market"] == market]
        market_summary[market] = {"unique_selected_legs": len(subset),
                                  "won": sum(leg["status"] == "won" for leg in subset),
                                  "lost": sum(leg["status"] == "lost" for leg in subset),
                                  "zero_production_losses": sum(leg["status"] == "lost" and leg["observed"] == 0 for leg in subset)}
    outcomes = {(ticket["game_id"], ticket["type"]): grade["status"] for ticket, grade in zip(tickets, grades)}
    nested = {"three_won_five_lost": sum(outcomes[(game_id, "3")] == "won" and outcomes[(game_id, "5")] == "lost"
                                           for game_id in {ticket["game_id"] for ticket in tickets}),
              "five_won_eight_lost": sum(outcomes[(game_id, "5")] == "won" and outcomes[(game_id, "8")] == "lost"
                                          for game_id in {ticket["game_id"] for ticket in tickets})}
    losses = [leg for leg in unique.values() if leg["status"] == "lost"]
    opportunity_diagnostics = Counter()
    for leg in losses:
        if leg["market"] == "anytime_td":
            continue
        prior_records = [boxes[game_id]["teams"][leg["team_id"]].get(leg["player_id"])
                         for game_id in leg["prior_game_ids"]]
        prior_records = [record for record in prior_records if record is not None]
        past_stats = [record["markets"].get(leg["market"], {"yards": 0, "volume": 0}) for record in prior_records]
        past_volume = sum(record["volume"] for record in past_stats)
        past_yards = sum(record["yards"] for record in past_stats)
        current = boxes[leg["game_id"]]["teams"][leg["team_id"]].get(leg["player_id"])
        actual_volume = 0 if current is None else current["markets"].get(leg["market"], {"volume": 0})["volume"]
        if past_volume <= 0 or past_yards <= 0:
            opportunity_diagnostics["no_positive_prior_efficiency"] += 1
        elif actual_volume * past_yards / past_volume < leg["line"]:
            opportunity_diagnostics["below_line_at_prior_efficiency"] += 1
        else:
            opportunity_diagnostics["enough_volume_at_prior_efficiency_but_missed"] += 1
    thin = sum(leg["prior_appearances"] <= 2 for leg in losses)
    high = [leg for leg in unique.values() if leg["ranking_score"] >= .6]
    report = {"tickets": len(tickets), "by_kind": by_kind, "market_summary": market_summary,
              "tickets_changed_by_postgame_participation_filter": changed,
              "winning_tickets_changed_by_postgame_filter": changed_winners,
              "non_prior_history_links": bad_history, "nested_failures": nested,
              "unique_failed_legs": len(losses), "failed_legs_with_at_most_two_prior_appearances": thin,
              "yardage_loss_opportunity_diagnostics": dict(opportunity_diagnostics),
              "selected_score_at_least_0_6": {"n": len(high), "wins": sum(leg["status"] == "won" for leg in high)},
              "repeated_failures": [{"player": player, "market": market, "ticket_leg_losses": count}
                                    for (player, market), count in failure_counts.most_common(20)]}
    (output / "parlay-loss-leakage-summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = ["# Parlay losses and leakage audit", "",
             "The 146–338 record is a postgame participation-filtered reconstruction. Its selection uses didNotPlay=false from each game's final roster and is not a valid pregame backtest. All 484 tickets consult hindsight roster data. "
             f"The participation filter changed the exact legs of {changed} tickets; {changed_winners} of those changed tickets subsequently won. That is exposure to hindsight, not an estimate of how many wins the leakage caused.", "",
             "## Confirmed leakage and missing controls", "",
             "1. Postgame participation enters selection in active_parlays.filter_participants before select. It removes active-but-unused players as well as DNPs. A pregame active list cannot know who will actually play. The original active-only instruction was therefore implemented as a hindsight diagnostic, not a clean backtest.",
             "2. Rules and thresholds were chosen after reviewing 2026 outcomes. Weekly lagging alone does not remove this model-selection leakage; the entire audited period is development data. An untouched later period is required.",
             "3. Earlier final-box dates are checked, but their publication/revision availability before kickoff is not recorded. Final corrected statistics retrieved today are not certified historical snapshots.",
             "4. Ticket selection forces 3/5/8/TD in every game, with no minimum forecast quality, quoted price edge, or joint scenario probability. This is construction risk, not temporal leakage. The actual engine's correlation and remaining-opportunity audits are bypassed.",
             "5. TD selection uses prior TD frequency, not red-zone touches/end-zone targets. Yardage selection also uses prior hit frequency; it does not forecast current targets, carries or attempts. This cannot isolate injury, usage and script failures without further evidence.",
             "6. Fixed research thresholds and the absence of sportsbook combination quotes prevent profit/edge measurement. Rushing/receiving TD grading is narrower than full sportsbook anytime TD rules, which can cover return scores. These are market-contract limits, not evidence of positive performance.", "",
             f"Checked every prior-game link: {len(bad_history)} link(s) had kickoff at or after the target game. This verifies date ordering only; it does not remove the participation and model-selection leakage above.", "",
             "## Losing tickets", "", "| Type | Won | Lost | Losses with exactly one failed leg | Failed leg occurrences |", "|---|---:|---:|---:|---:|"]
    for kind, row in by_kind.items():
        lines.append(f"| {kind} | {row['won']} | {row['lost']} | {row['one_failed_leg']} | {row['failed_legs']} |")
    lines.extend(["", f"There were {nested['three_won_five_lost']} games where the 3-leg ticket won but the 5-leg lost, and {nested['five_won_eight_lost']} where the 5-leg won but the 8-leg lost. Adding marginal players created those observed failures; this does not prove what their fair price should have been.", "",
                  "## Unique selected leg results", "", "| Market | Legs | Won | Lost | Losses with zero production |", "|---|---:|---:|---:|---:|"])
    for market, row in market_summary.items():
        lines.append(f"| {market} | {row['unique_selected_legs']} | {row['won']} | {row['lost']} | {row['zero_production_losses']} |")
    lines.extend(["", f"Of {len(losses)} unique losing legs, {thin} had no more than two prior appearances. Such thin histories can promote backups or one-game spikes. Scores here are ranking frequencies, not calibrated probabilities.", "",
                  f"Of the unique yardage losses, {opportunity_diagnostics['below_line_at_prior_efficiency']} had fewer actual attempts/carries/targets than needed at prior observed efficiency; {opportunity_diagnostics['enough_volume_at_prior_efficiency_but_missed']} had sufficient volume by that counterfactual but missed through lower realized efficiency. These are diagnostic comparisons, not established causal attributions. TD failures require separate red-zone opportunity analysis.", "",
                  "## Failures repeated across tickets", "", "| Player | Market | Failed ticket-leg occurrences |", "|---|---|---:|"])
    for row in report["repeated_failures"]:
        lines.append(f"| {row['player']} | {row['market']} | {row['ticket_leg_losses']} |")
    lines.extend(["", "## Every losing ticket and failed leg", "", "| Week | Game | Type | Failed legs |", "|---:|---|---|---|"])
    for ticket, grade in zip(tickets, grades):
        if grade["status"] == "lost":
            detail = "; ".join(f"{leg['player']} {leg['market']} {leg['observed']}/{leg['line']} (prior appearances {leg['prior_appearances']})"
                               for leg in grade["legs"] if leg["status"] == "lost")
            lines.append(f"| {ticket['week']} | {ticket['game']} | {ticket['type']} | {detail} |")
    lines.extend(["", "## Required clean replay boundary", "",
                  "Use sourced pregame team membership and active/inactive announcements with a published/observed time before the decision cutoff. Keep active-but-unused players eligible. Freeze the candidate rules and model before the evaluation period, permit an explicit pass, and use the real engine's scenario/correlation audits. Outcomes and participation records may enter settlement only. The original and participant-filtered artifacts remain available as diagnostics, not validated wagering records.", ""])
    (output / "parlay-loss-leakage-audit.md").write_text("\n".join(lines), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.cache), indent=2))
