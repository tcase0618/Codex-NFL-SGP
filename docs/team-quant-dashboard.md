# Team Quant: implemented scope and correctness boundaries

Implemented November 3, 2026. Before this change the repository had descriptive
final-box team profiles and a player terminal. It did not have the requested
401-field team dashboard. This change adds that dashboard and a shared,
source-versioned play store without representing missing charting as facts.

## Catalog and actual coverage

The catalog preserves exactly 401 proposed slots across the 18 requested
categories, with the specified category counts and 24 analytical lenses. Some
slots describe the same underlying calculation; they are not 401 independent
features. Supported aliases share feature signatures and confidence resamples.
There are 166 direct public calculation slots (150 distinct direct rules), plus
supported rolling/trend transformations. Actual population depends on the team,
year, view and source-field coverage. Unsupported fields remain null with reasons.

Minimum samples are explicit research screening policies: generally 100
eligible plays and three games, or 20 drives / three games for drive measures.
Game-level quantities use their own game exposure. These are not validated
predictive thresholds. Small-sample values remain visible but are not ranked.

## Canonical evidence

The imported archive contains 935,616 source play records from 5,320 reported
regular-season finals spanning 2006–2026, covering all 32 current franchises.
All 21 requested season files and the game/schedule file were available. No
historical pregame availability is inferred from their current collection.
Current-season schedule scores were compared with matching archived ESPN finals.
All 121 Week 1–8 games are covered. Independent production reconciliation checks
compare nine basic fields in each of 242 team-games (2,178 comparisons).

Historical SD/OAK/STL/WAS/LA codes map to their current franchise keys. Original
codes remain in play data. Player events join by explicit GSIS IDs, never by
name. The database contains games, plays, a source-drive view, formations,
player events, player/team metric snapshots, metric definitions and source proofs.
Lineups, all-11 participation, charting, injuries and model-prediction tables are
reserved but not populated without verified inputs. Coarse formation flags do
not establish personnel counts, motion, coverage shells or assignments.

Existing 32-team profile generation includes catalog references. A separate
32-team export binds the actual play-derived metrics to the reviewed profiles,
checks knowledge times and current-season game coverage, then hashes the result.
Changed profile versions invalidate existing scenario bindings through the
existing matchup/ledger workflow; they do not invent replacement forecasts.

## Calculation decisions checked during implementation

- Raw yards, attempts and play counts retain recorded kneels and spikes. EPA
  and competitive success measures exclude them and two-point tries.
- NFL throw attempts exclude sacks. Called-dropback EPA and sack rates include
  recorded scrambles. Exact event groups and denominators accompany each value.
- Known incompletions contribute zero passing yards, preserving their attempts.
  Unknown fields remain missing rather than becoming zero.
- Team rushing production uses full run-play yardage, not only primary-rusher
  credited yards; the latter missed a recorded lateral yard in one Patriots game.
- Net passing production does not credit scramble rushing yards as passing yards.
- Drive scoring uses observed score changes, including kick-only scoring
  possessions. Touchdowns are not assumed to be worth seven points. Drive EPA
  includes terminal punt/field-goal EPA; competitive-play EPA is a distinct scope.
- Defensive fumble and takeaway credit uses reported team attribution. A fumble
  by an interception returner is not blindly credited to the original defense.
- Half/game-state filters do not relabel full-game or full-drive totals. Defense
  leading/trailing reverses the possession-side score differential.
- Calendar days since a previous observed regular-season game are calculated
  from dates within the selected season. The first observed game is unavailable;
  a provider's week-one rest convention is not treated as an observed interval.
- Long rest is not automatically called a bye. Exact post-bye classification is
  unavailable without a verified classification contract.
- Directional midrank percentiles use qualified same-season peers. Lower EPA
  allowed and lower sack exposure rank higher; deployment frequencies retain
  neutral raw-value ranks. A higher frequency is not automatically better.
- Confidence intervals use 400 deterministic whole-game cluster bootstrap
  samples for supported estimators with at least five observed game clusters.
  They are descriptive, not predictive. Year-over-year changes, trend slopes
  and percentile ranks do not borrow season-level intervals.
- Year-over-year results require matching observed week windows. Three-year
  totals with unequal exposure are withheld. Comparisons retain all contributing
  source IDs. EPA/CPOE/expected-completion inputs are provider-model-derived;
  their original training vintages have not been independently certified.

## Dashboard and shared player layer

The team dashboard is `/teams` in the same local terminal. It supports all 32
teams, all 401 field slots, the 24-lens selector, observed-only/search filters,
directional percentiles, game trends, offensive/defensive gap matrices,
coaching/caller context, source hashes, record-level evidence and JSON export.
Historical seasons never inherit current coaches or personnel.

The matchup lab compares both directions of a selected pairing with actual
values and qualified cohort ranks. Where measures have different denominators,
their labels retain that distinction. Ranks are not subtracted into an edge.
Expected scores, win probabilities, personnel-adjusted forecasts and interaction
models remain unavailable until a model is trained and evaluated appropriately.

Player profiles now expose a **Shared play data** tab from the same canonical
plays used by team metrics. It reports recorded passer/receiver/rusher events,
basic production and offensive EPA on those events, with source identities.
That is not all-11 participation or causal player credit. Existing independent
provider/NGS aggregates remain explicitly separate corroborating observations.

## What is not completed

Coverage shells, man/zone responsibilities, inside/outside zone versus gap/power
concepts, full lineups, pressure attribution, grades and personnel impact models
are not inferred from ordinary play-by-play. Rushing direction is not a run
concept. Sacks and hits are not substituted for pressures. Current-vintage data
does not establish what was known at old kickoffs.

Opponent adjustment, top-/bottom-10 opponent splits, weather/travel effects,
causal coaching adaptation and matchup score distributions need additional
versioned inputs and validated models. The requested illustrative 88.7 grade
and synthetic matchup numbers are not installed as production data. No offered
prices, wagers, predictive advantage or profitable backtest is asserted.

The numerical play store is a versioned import. Existing automatic roster/news
refreshes continue and inherit the metric catalog; automatic re-ingestion of
new advanced play data is not enabled by this change. A stale/missing numerical
snapshot must be rebuilt, not treated as a fresh forecast.

## Operation

`research.collect_team_quant --output <new-archive>` archives source files.
`research.build_team_quant --root <task-root> --archive <archive> --output
<new-snapshot> --player-database <player-snapshot>/players.sqlite` creates a new
store without overwriting observations. Add `--team-database <snapshot>/teams.sqlite`
to `research.player_terminal` to enable the team dashboard and shared player
views. The server is read-only and binds to 127.0.0.1.

The output includes an integrated all-32 profile export, reconciliation report,
source/catalog hashes, browser screenshots, verification report and a local
PowerShell launcher. The full regression suite covers catalog counts, temporal
cutoffs, attribution, correct denominators, aliases, confidence boundaries and
profile binding. No execution or betting endpoints were added.

Primary reference: https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html

## Final verification artifacts

The canonical integrated export is `all-32-integrated-profiles-final.json` in
the task's `outputs/team-quant-20261103-v2` folder. Earlier exports are retained.
Visual QA caught an allowed-pass-rate direction rule taking precedence over
defensive ranking. The corrected definition revision ranks lower explosive
pass rates allowed as better; regression assertions cover that precedence.
`metric_definition_versions` preserves the final catalog alongside the original
import catalog. Numerical observations are unchanged by this ranking correction.
`verification.json`, `api-checks.json`, `browser-checks.json` and
`completion-hashes.json` record the checks and exact delivered versions.
