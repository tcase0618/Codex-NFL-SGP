# Mathematics audit — 2026-11-06

## Verdict

The engine's arithmetic has useful internal guards, but **predictive accuracy and a betting edge are not established**. `Decision.approved` means structural research eligibility only. The live dashboard's team matchup percentiles are descriptive; it returns no score distribution or win probability. The weather alert thresholds are uncalibrated research screens and do not change projections.

## Checks and correction

| Area | Finding | Disposition |
| --- | --- | --- |
| Poisson opportunity tail | Numerical values at rates 0.1, 1, 5, 10, 100, 1,000 and 10,000 agreed with SciPy's Poisson survival function in a local spot check. This verifies arithmetic on those inputs, not the Poisson workload assumption. | No formula change. |
| Joint sample consistency | Previously, 100 all-true samples could replace a materially lower scoring marginal and yield a 100% parlay probability. Sample count and Boolean shape alone did not prevent this. | Block samples whose per-leg frequencies diverge from the scenario model's marginals beyond `sqrt(log(2 * legs * scenarios / 0.01) / (2 * samples))`. The value is a lenient internal screen. Its nominal statistical interpretation requires independent draws, which source metadata does not prove. |
| Scenario weight total | A tolerance of `1e-6` admitted scenario weights above one and could inflate a probability that remained below one; a separate range check blocked values above one. | Tightened sum-to-one tolerance to `1e-12`; inconsistent weights fail before a probability is returned. |
| Combined decimal quote | Break-even probability is `1 / decimal_odds`; illustrative unit-stake net return is `joint_probability * decimal_odds - 1`. | Arithmetic is correct for a single exact combined gross quote without fees or void repricing. The estimate is research-only because its probability is not prospectively calibrated. |
| Weather alert | A 15 mph wind, below-40°F temperature, or 30% precipitation-chance threshold triggers review, not an estimated point or yardage change. | No weather coefficient is applied. Forecast completeness, freshness, venue identity and roof exposure remain separate gates. |

The tests now reject contradictory joint samples and overweighted scripts. Existing synthetic fixtures were adjusted to use sample rates compatible with their own model assumptions. The full suite passed: **1,223 passed, 1 skipped**. The skip is not counted as a pass.

## Mathematical assumptions still needing prospective validation

1. **Opportunity distribution.** Yardage probability treats remaining carries, targets or attempts as Poisson and yards per opportunity as a fixed scenario efficiency. Real volume and per-action yardage distributions, playing time, game state and injury are more variable. The hard-cap and script checks reduce some impossible cases but do not calibrate the tails.
2. **Scoring opportunity.** Anytime TD uses `1 - exp(-rate * expected_scoring_chances)` under a Poisson thinning model. This is not equivalent to a fixed number of independent scoring chances and needs prospective calibration by role and game script.
3. **Joint simulation.** A scenario requires at least 100 shared samples. The new marginal check catches stark contradictions, but 100 samples have wide uncertainty, and metadata does not establish independent draws, correct simulation code, or real-game calibration.
4. **Scenario weights and stress rule.** Weights and the 5% per-scenario ticket stress threshold are supplied heuristics. There is no verified fitting procedure or out-of-sample evidence that these choices improve ticket decisions, particularly for 8-leg tickets.
5. **Historical reconstruction.** The 2026 Week 1–8 replay and 2025 calibration use lagged boxes but lack fully archived pregame source vintages, game-day eligibility, actual offered combination prices and prospective ticket freezes. The historical parlay summary is not a placed-bet win/loss record and cannot validate ROI.
6. **Market settlement.** The unit-stake price formula omits fees and bookmaker-specific push, void and repricing rules. Those must be applied from the exact offered contract before account ROI can be calculated.

## Required validation before betting-readiness claims

Freeze source versions, active/inactive evidence, model parameters, joint-simulation seeds and code version, exact offered lines and combined prices before kickoff. Grade only those frozen tickets from final authoritative results. Compare both leg and full-ticket calibration against simple baselines by walk-forward season, with uncertainty intervals and a separate untouched holdout. Test weather effects against archived **pregame forecasts** and final conditions before introducing a weather coefficient. Neither a green test suite nor a hash chain authenticates historical source availability or proves predictive skill.

## Follow-up gap closure on 2026-11-06

The second audit reproduced and closed five additional failure paths:

1. **Pregame totals:** A pregame leg with `current >= line` previously evaluated at probability 1, despite the game not having started. Pregame `current` must now be zero in both the engine and the frozen ledger contract. Genuine live fixtures retain nonzero totals.
2. **Overlapping touchdown chances:** Red-zone touches and end-zone targets can name the same play. When both are positive, the engine now requires `unique_scoring_chances` with a value between the larger component and their sum. Without that deduplicated count, the TD leg blocks. A live deduplicated count also requires a corresponding remaining-opportunity cap. This prevents double-counting but does **not** validate the shared conversion rate across chance types.
3. **Forged approved decisions:** Direct ledger append previously accepted an approved decision with plausible probability bounds even when it differed from the engine's result. Approved proposals are now recomputed from their frozen typed snapshot, legs and evaluation controls; exact decision mismatch blocks append and replay. Rejected research records remain allowed but cannot enter prospective performance scoring.
4. **Relaxed controls:** Source age above 300 seconds, a scenario stress floor below 5%, or an aggressive-line fraction below 0.8 can no longer approve a decision. Price comparisons likewise reject a source-age setting above 300 seconds. These are structural defaults, **not empirically optimized thresholds**.
5. **Incomplete weather hours:** A missing wind, temperature or precipitation probability in any hour of the game window can no longer produce `NO_SCREEN_TRIGGER`. A missing or future forecast generation time is unavailable. A known trigger with other fields missing remains a watch with incomplete-field wording.

The full Python suite passed after these changes: **1,233 passed, 1 skipped**; the focused Matchups JavaScript suite passed **4 tests**. Local code and fixture checks establish internal behavior only. Existing approved ledger rows generated under an earlier formula may fail the new replay check; they must be retained and reviewed as historical evidence, not silently rewritten or counted as current validated forecasts.
