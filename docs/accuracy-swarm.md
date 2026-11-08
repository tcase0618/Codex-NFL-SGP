# Accuracy audit framework

Seven executable audit roles now complement the existing eight processing
skills: source, roster, market, remaining opportunity, scenario/backtest,
correlation/calibration, and ledger. The repeatable runner executes the seven
checks concurrently and writes immutable JSON and Markdown artifacts. A missing
role, exception, changed input, unsupported measurement or missing evidence blocks
the aggregate result. A majority vote cannot override a blocker.

The initial six coding agents stopped at usage limits. They were subsequently
resumed, and a seventh ledger reviewer completed its assigned review. All seven
bounded code reviews are now complete. The parent integrated the findings and
fixed two final ledger edge cases: final evidence cannot predate a live proposal,
and rejected forecasts may retain observed outcomes with unavailable probability
while remaining excluded from performance scoring. The final shared suite passes
704 tests. No billing changes or paid API setup were performed.

The completion artifacts are in the task workspace under
`outputs/agent-review-completed-20261104-final`. All 32 derived team profiles were
rebuilt with the reviewed calculation and definition revisions. Basic production
reconciliation matched 2,178 checks across 242 team-games with no differences or
missing values. This does not certify advanced metrics or historical pregame
knowledge. The seven-role data readiness audit remains BLOCKED; completed code
reviews do not establish predictive accuracy or deployment readiness.

## Actual changes

- Engine validation rejects nonfinite values, boolean numerical inputs, truthy
  strings used as active flags, malformed sources, invalid clocks, contradictory
  joint samples and unsupported opportunity ranges. Probability-tail arithmetic
  handles large supported means without the old underflow failure.
- Decisions expose `use_status=RESEARCH_ONLY` when structurally approved;
  recorded decisions retain that distinction. Approval is not proof of skill.
- Team calculations now distinguish known versus missing EPA exposure, counts
  versus percentages, percentage points, pressure and punt directions, net
  passing versus scramble production, and known gap samples. Confidence
  intervals do not silently omit denominator games with zero eligible events.
- Injury parsing retains future practice days as unknown, separates official
  club tables from opposing tables, detects incomplete scopes and preserves
  historical reports separately. Pagination repeated content cannot certify
  complete transactions. Reviewed identities must survive source revalidation.
- Backtest evidence requires real inspectable archives, exact frozen tickets,
  explicit game-specific active players, forecast probabilities, quote bindings,
  separate full-ticket settlement, and forward train/test timing. Current-vintage
  reconstruction does not become prospective validation.
- Calibration rejects malformed and one-class training, validates chronological
  evidence and recomputes supplied Brier/log-loss comparisons. Evidence
  consistency is distinct from sufficient samples, improvement or profitability.
- Ledger settlement cannot precede kickoff, replace frozen probabilities or
  odds, omit legs or substitute a different player/threshold. A broken hash
  chain cannot produce prospective performance metrics. Mixed void outcomes
  remain unresolved without applicable sportsbook settlement rules.
- Final-box grading binds the exact official endpoint event ID and body game
  ID; duplicate player-market rows and nonfinite totals cannot inflate outcomes.
- `construct_audited_tickets` requires matching game/model/profile versions,
  linked artifact hashes and independent re-execution of all seven checks.
  A saved JSON report containing PASS labels cannot itself authorize candidates.
- Clearance also binds the exact snapshot and offered-leg contents, including
  prices, thresholds and current totals; changes invalidate prior clearance.
- Week-ordered reconstruction is explicitly labeled as unverified historical
  inputs, with unavailable metrics left null. All incomplete injury-report
  statuses block eligibility until the missing evidence is supplied.

## Running the audit

From the football repository:

    python -m research.accuracy_swarm --config <refresh-config.json> --team-database <teams.sqlite> --output <new-folder> [--evidence <manifest.json>]

The output folder must be new. The audit reads the current immutable profile
snapshot from the refresh state; it does not refresh feeds, create wagers or
mutate the source databases. It verifies archived source hashes, including old
ESPN cache names that hash URLs rather than response content.

The optional evidence manifest has separate `scope`, `derived_metrics`,
`opportunity_snapshot`, `backtest`, `calibration` and `ledger` sections. Missing
sections block their associated roles. A scope identifies the exact game,
model version and team-profile versions. Opportunity and ledger sections require
absolute artifact paths and matching SHA-256 hashes. The backtest module documents
its full archive contract; the calibration tests show synthetic format examples.
Those fixtures are never performance evidence.

    python -m research.rebuild_team_derivations --profiles <profiles.json> --database <teams.sqlite> --output <new-derived-file.json>

This creates a new all-32 derived export, binding the current calculation and
definition revisions to the existing raw store and source-profile hashes.
Previous snapshots are retained. New calculations do not backdate source
availability, fill missing charting, or certify pregame knowledge.

## Remaining accuracy work

The delivered artifact audit remains BLOCKED: current source gaps, incomplete
stable identities and game-day active universes, missing all-11/tracking inputs
for the requested interaction models, no frozen game-scoped forecast/quote
bundle, no valid prospective backtest/calibration manifest, and no supplied
settled prospective ticket ledger. There is no measured accuracy percentage or
validated betting edge. Neither a test count nor 401 metric definitions supplies
those missing observations.

The next valid evaluation uses frozen inputs collected before decisions, explicit
offered markets and combined quotes, and final settlements after the games.
Acceptance criteria and sample sufficiency must be selected before evaluating
an untouched period. This change does not create a background LLM agent service
or silently alter the existing football-refresh automation.
