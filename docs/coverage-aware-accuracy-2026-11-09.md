# Coverage aware accuracy check, November 9, 2026

The reconstructed 2026 Week 1–8 leg forecasts contain 2,481 rows at the `(season, week, game, team, player, market, line)` grain. Of these, 2,028 have a binary outcome and 453 remain unresolved. The [audit tool](../research/coverage_aware_accuracy.py) verifies unique grain and that the calibrated CSV retains every raw forecast and outcome exactly. It hashes both inputs and computes Brier scores on resolved rows plus paired bounds over every possible binary label for unresolved rows.

| Scope | Rows | Resolved | Unresolved | Raw Brier, resolved | Calibrated Brier, resolved | Calibrated minus raw over all rows, feasible range |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Weeks 1–8 | 2,481 | 2,028 | 453 | 0.2070 | 0.1841 | −0.0585 to −0.0103 |
| Week 1 | 354 | 193 | 161 | 0.1950 | 0.1785 | −0.1082 to +0.0113 |

The paired all-row range uses the **same unknown outcome** for the raw and calibrated probability on each unresolved row. The negative overall range means the archived calibrated probabilities score better than the archived raw probabilities for every assignment of those unresolved binary outcomes. Week 1 does not meet that stronger condition. This comparison does not show whether either model beats a sportsbook price, nor whether a joint 3/5/8-leg probability is calibrated.

These bounds assume each unresolved row eventually has a binary result. If a row is actually void or its player was ineligible, the scored population changes, so the bounds do not establish that comparison.

The input files are `outputs/replay-2026-predictions.csv` and `outputs/replay-2026-calibrated.csv` in the Codex task workspace. The saved report is `output/audits/2026-11-09/coverage-aware-replay-final/`. The raw CSV SHA-256 is `5506a5b7176e321abadec61e7a3c9481378cbb181a3cd16646f6191d0cfda90b`; the calibrated CSV SHA-256 is `ce91aef0944e62b3acfc40cb4e5a011e480cdefb61a727dfad027c0e8d8a250b`. The script records and checks the actual hashes in its JSON report.

These are **retrospective reconstructed forecasts**. Historical source versions, game-day active status, offered FanDuel lines, combined SGP prices, and frozen pregame selections are not certified. A resolved box-score row is not a verified settled wager. No change to the probability formula is promoted from this analysis alone; the next accuracy claim requires a prospective holdout with those inputs frozen before each game.
