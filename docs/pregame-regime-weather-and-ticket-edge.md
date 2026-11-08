# Pregame regime, weather and ticket-edge design

## Why this exists

The November 5, 2026 Jaguars-Ravens research flagged Joe Fagnano's first start and possible rain, but the larger constructions still used Baltimore receiver and Henry touchdown legs without a quantitatively validated joint stress case. Henry cleared both rushing-yard floors (57 yards) while Baltimore had zero red-zone trips; Flowers finished with six receiving yards; Jacksonville still passed for 242 yards in the wet game. These are observed outcomes, not training weights or evidence that rain caused the Baltimore passing failure.

The current `football_engine.engine.Scenario` supports scenario-specific remaining opportunities, yards per opportunity, touchdown rates and joint samples. It does not itself require a quarterback-regime or weather-specific scenario, prove the scenario weights, or provide a forecast-weather cutoff contract. The `football_engine.team_metrics` library lists QB availability and weather metrics, but definitions alone do not establish that those metrics are measured or used in a forecast.

## The pregame state that must be frozen

For each candidate game, archive an append-only pregame decision record with kickoff, decision time, player/team IDs, official QB starter announcement, the starter's available prior dropbacks, injury and practice status, backup contingency, projected lineups, venue/roof, timestamped NWS hourly forecast and any updated nowcast, exact market line/rule/book/price, source hashes and model version. Revalidate final actives, QB and weather close to kickoff. A forecast captured in the morning cannot establish evening conditions. No postgame weather or box score may be inserted into that record.

Missing or late inputs get `UNKNOWN`; they do not receive a neutral zero effect. A ticket requiring that input remains research-only until a defensible scenario is supplied and evaluated.

## Forecast components, in causal order

| Component | Candidate features | Outputs needed by tickets | Data/evidence boundary |
|---|---|---|---|
| QB regime | Announced starter ID; prior NFL dropbacks, starts and snaps; change from the unit's historical QB; injury/practice trend; short-week preparation; protection; opponent rush/coverage | Attempts, completion and sack/turnover distributions, not a single QB rating | Shrink one-game observations strongly; never transfer Jackson/Huntley team passing averages unchanged to Fagnano |
| Weather | Roof; hourly precipitation probability, expected amount/intensity when available, wind/gust and direction, temperature, updated forecast age and uncertainty | Scenario uncertainty for play selection, completion, fumbling, kicking and pace | NWS precipitation probability is a chance of measurable precipitation, not a percentage reduction in yards; coefficients require held-out estimation |
| Game script | Possession count, starting field position, third-down conversion, turnovers, score margin by quarter | Team plays, pass/run mix and drives reaching scoring position | Simulate competing scripts, including early lead and failed drives, rather than conditioning only on final score |
| Player use | Routes, target share conditional on QB and personnel, carries conditional on lead, red-zone role | Player target/carry distributions and efficiency | Availability and role sources must be known before kickoff; injury does not justify invented extra targets |
| TD allocation | Drive arrival at red zone, goal-line snaps, player share, conversion; separate long-score path | Passing TD and anytime TD distributions | Yardage is not a TD proxy; Henry's carry volume cannot generate a goal-line touch on a drive that never arrives |
| Market | Exact offered threshold, settlement rules, timestamped executable quote, SGP combined quote and restrictions | Break-even probability, calibrated edge and price sensitivity | Singles cannot be multiplied into an executable same-game price |

## Scenarios to require when material

When a projected starter has little NFL evidence or differs materially from the quarterback behind the team's historical aggregates, require a `new_starter_low_output` scenario alongside base and upside cases. For an outdoor game with a material, uncertain forecast, require dry/base, wet, and delay/interrupt scenarios. Evaluate their *cross-product* where relevant: a new QB in rain may have a different distribution from either factor alone. If evidence cannot support weights, show stress ranges and block a positive-edge or bet-ready claim. Do not assign a made-up multiplier.

Gate the **ticket**, not just each leg. A receiver over, the same team's anytime TD, and an opponent receiver over may respond differently to the same game script. All legs must be evaluated on the same simulated play/drive sample. Retain a low-volume, opponent-blowout, no-red-zone and long-explosive-score scenario even when they reduce the apparent probability. Reject a candidate if a plausible scenario destroys the joint outcome and its risk cannot be priced.

## Where a genuine market edge might appear

- A market can be slow to reprice a replacement QB's effect on *receivers, drive arrival and touchdown allocation*, even after repricing the QB's own yardage. Test receiver unders, team totals and specific TD No markets against actual offered prices; do not assume they are cheap.
- A market may treat a running back's carries and anytime TD similarly. A run-heavy protection plan can raise carries while poor drives reduce touchdown opportunity. Compare rush-yard ladders and ATD separately.
- Weather may be mispriced when a fresh radar/forecast update changes the distribution after a morning market snapshot. Measure the update's timestamp and offered-price response. Light rain with low wind is not automatically an under; Jacksonville's 242 passing yards in this game is a counterexample to a universal penalty.
- For parlays, a ticket's marginal edge can disappear when two legs share a single scoring drive or explosive play. Compare the joint simulated outcome to the actual combined same-game quote. Avoid filler legs added for payout.

These are candidate inefficiencies, not established profitable bets. No recommendation is actionable without an exact fresh offer, eligibility, a calibrated joint model and price edge after fees and uncertainty.

## Validation before giving these factors weight

1. Build historical **as-of** QB announcements, injury/status and timestamped weather forecasts; final weather and box scores are labels only. Missing archived forecasts stay missing.
2. Fit only on games completed before the evaluation week. Preserve each fitted version and frozen ticket. Compare baseline, QB-only, weather-only and interaction models on untouched later games.
3. Evaluate attempts, completions, receiving yards, drive red-zone arrival and TDs separately with proper scoring rules and calibration by starter-experience and weather strata. Use uncertainty intervals; avoid a threshold chosen after seeing the Jaguars-Ravens result.
4. Evaluate ticket joint calibration and actual market edge using captured combined quotes, fees, stale-price checks and closing-line value. Report denominators, unavailable offers, no-bet decisions, losses and wins separately. A positive hit rate alone is not proof of value.
5. Promote a feature only if it improves held-out accuracy or decision quality and survives an ablation. Otherwise retain the factor as an explicit stress scenario or veto, not a fabricated probability adjustment.

## Immediate next engineering cut

Add a pregame context object and source-validated `QB_REGIME_UNKNOWN` / `WEATHER_STALE` statuses to ticket evaluation. Require named downside scenarios for affected legs, and keep `joint_probability=None` when scenario weights or shared samples are unverified. Do not silently change existing model probabilities. Then run regression fixtures for Fagnano-like replacement-QB plus wet forecast, veteran QB plus dry game, a rush-yard hit with no red-zone trip, and a long receiving TD without a red-zone target.

## Sources

- Pre-game starter and Week 8 evidence: https://www.nfl.com/news/ravens-to-start-undrafted-rookie-qb-joe-fagnano-vs-jaguars-on-thursday
- Official rain-soaked game recap: https://www.baltimoreravens.com/news/ravens-jaguars-game-recap-routed-week-9-2026
- NWS precipitation-probability definition: https://www.weather.gov/ppg/forecast_terms
- nflverse play-by-play and game metadata coverage: https://github.com/nflverse/nflverse-data/blob/main/README.Rmd and https://github.com/nflverse/nfldata/blob/master/DATASETS.md
