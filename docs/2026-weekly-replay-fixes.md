# 2026 Week 1–8 replay corrections

The original research replay scored 2,481 illustrative yardage lines from 121 games. Its fixed Poisson volume/efficiency forecast assigned 0% to some eventual hits and overstated many high-confidence lines. The replay also left player-market rows unresolved when a player appeared in another offensive section of the same final box.

## Implemented

- A player with any offensive final-box appearance is graded at zero yards for a missing yardage market. A player absent from the whole offensive box remains unresolved because the feed cannot distinguish inactivity from no use. This resolved 78 additional 2026 rows without using final-game participation to choose pregame candidates.
- `research.calibration` fits a regularized, monotonic logistic mapping separately for passing, rushing, and receiving. It clips raw 0% and 100% inputs only inside the mapping so fitted probabilities remain finite.
- `research.forward_calibrate` fits on 2025 Weeks 5–18 and, before each 2026 week, refits using only prior completed 2026 weeks. It keeps the raw forecast and calibrated probability side by side. Current-week outcomes enter training only after current-week predictions are written.
- Tests check missing-market zero grading, absent-player unresolved grading, extreme probability behavior, and that changing a future-week outcome cannot change an earlier forecast.

On the corrected 2,028 graded 2026 rows, raw Brier score was **0.2070** and forward-calibrated Brier score was **0.1841**. Every week improved, but the fitted probabilities still have calibration gaps and the sample excludes 453 unresolved rows. The calibration method was selected after reviewing the 2026 errors, so this comparison is retrospective model development even though each coefficient fit uses only older games. An untouched later period is required for independent confirmation. The improvement is a research result for fixed yardage singles, not a betting return.

## Still unverified

The feed has no archived pregame roster or exact sportsbook combination quote. This replay also lacks pregame red-zone opportunity and a joint game simulation. Actual player eligibility, touchdowns, live gates, parlay correlation, and price edge remain outside its measured scope. The prospective ledger and price screen retain their fail-closed boundaries.

## Historical parlay constructions

The separate `research.parlay_replay` tool now selects exactly defined 3-, 5-, and 8-leg same-game research tickets from the calibrated yardage forecasts. Selection strips outcome fields, requires distinct players, both teams, and a same-team appearance earlier in the 2026 season, and saves a manifest with the exact legs and a source-projection hash. Week 1 is withheld because a 2025 team appearance does not prove 2026 eligibility. The first replay version failed this gate and was superseded; its artifact remains marked as such. The tool grades saved tickets afterward and leaves tickets unresolved when an ungraded leg is the only obstacle to a win. The output records statistical parlay hits and misses, not placed-wager wins or profit. The selection rule was chosen after inspecting the 2026 season, so its record is exploratory and requires untouched future validation.
