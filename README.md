# Case Capital Football Betting Engine

Research and ticket screening engine for football player props. It accepts external, timestamped roster, market, game-state, and scenario snapshots. It does not fetch prices, place bets, or infer historical outcomes.

The [November 8 Sunday remediation](docs/nov-08-2026-root-cause-remediation.md) documents the cutoff, fresh-snapshot, quarterback-change, pricing, and weekly-display controls added after the retrospective ticket audit. Historical research threshold clears and late revisions are not prospective betting results.

The [coverage aware accuracy audit](docs/coverage-aware-accuracy-2026-11-09.md) tests how unresolved player outcomes affect the reconstructed Week 1–8 Brier comparison. Run `python -m research.coverage_aware_accuracy --source <calibrated.csv> --baseline <raw.csv> --output <new-directory>`; the output directory must not exist. This does not validate same-game parlay probabilities or prices.

## November 2 postmortem changes

- Touchdown probability uses remaining red-zone touches and end-zone targets with a separate conversion rate. Rushing or receiving yards do not stand in for scoring chances.
- When red-zone touches and end-zone targets may overlap, provide `unique_scoring_chances` backed by the scenario source; without it, a TD leg with both positive components is blocked. Pregame leg `current` totals must be zero.
- Yardage lines use current totals, remaining targets/carries/attempts, and scenario-specific efficiency. A line is rejected when its remaining requirement exceeds the workload allowance in any meaningful scenario.
- Live feeds can provide `maximum_remaining` opportunities per player and scenario. Any line needing more actions than that hard cap is rejected, even if a mean forecast appears favorable.
- Live proposals require a fresh `GameState` with an in-progress clock and a scenario-specific hard cap for each yardage leg. The ledger freezes live proposals separately from pregame proposals and rejects retrospective backfills.
- Each game script has an explicit weight and opportunity forecast. Ticket probability is a weighted mixture of scenario-level joint probabilities, preserving common game-script dependence across legs. The scenario stress gate rejects tickets that rely on one favorable script.
- Multi-leg tickets fail the correlation audit unless every scenario supplies at least 100 shared `joint_samples` with an outcome for each leg. Those samples must be generated from one consistent game simulation; sample count alone does not establish calibration.
- Player ID, team, active status, and eligible markets are checked before a ticket can pass. Each roster, price, and scenario input carries a provider, reference, and observation time. Missing, future, and stale provenance fails closed.
- Eight named processing skills (`SKILLS`) and seven named auditors (`AUDITORS`) appear in every decision. Auditors return pass/fail, while reasons retain exact leg and scenario details.
- `Ledger` stores immutable pregame, live, and settlement events in SQLite with a hash chain and update/delete triggers. A settlement requires a frozen proposal. Unknown leg totals remain `unresolved`.
- Pregame records must be saved before kickoff; historical tickets cannot be backfilled as prospective forecasts. Append operations lock the ledger while extending its hash chain.
- `fetch_final_box` captures the ESPN final summary with retrieval time and content hashes. `settle_recorded_ticket` grades only a previously frozen ticket against a matching final box; missing player statistics remain unresolved.
- `compare_ticket_prices` ranks alternative constructions using an observed combined `TicketQuote`, its break-even probability, and illustrative expected return. Missing or stale quotes are excluded. These estimates are research-only because the joint model has not been independently calibrated.
- A combined quote must identify the exact leg keys, bookmaker source, and timestamp. `screen_prices` returns `NOT MEASURABLE` when no valid price exists and never labels its ranking an executable bet.
- `Ledger.prospective_report()` excludes unresolved and unapproved forecasts, requires 100 settled forecasts by default before labeling research performance measurable, and never infers account ROI from suggested tickets.

## Inputs and operation

The repeatable [accuracy audit](docs/accuracy-swarm.md) runs seven independent
evidence checks over the actual profile and play-store artifacts. Use
`construct_audited_tickets` for candidates requiring that gate; saved PASS labels
alone cannot authorize them. Existing structural decisions expose
`use_status=RESEARCH_ONLY`. Predictive accuracy remains unverified until valid
prospective evidence is collected and evaluated.

The audited constructor now requires a frozen, hash-matched FanDuel Tennessee
offer capture **before** it selects ticket legs. See the
[market first workflow](docs/market-first-workflow.md). A capture proves only
structural consistency; a human-reviewed source, the exact combined sportsbook
quote and independently validated joint probabilities are still needed before
any claim of favorable odds. Historical replay thresholds are never live offers.
For quoted, audited candidates, `rank_priced_parlays` shows the best available
probability/payout tradeoffs while retaining `RESEARCH_ONLY` status; its 50%
joint-probability screen is a configurable preference, not a win guarantee.

Construct `Source`, `Player`, `Leg`, `Scenario`, and `Snapshot` objects from a trusted feed, then call `evaluate_ticket(snapshot, legs)`. Inputs require timezone-aware timestamps. For live evaluations, supply `GameState(seconds_remaining, "in_progress", source, {"CHI": 20, "SEA": 9})` and every relevant `maximum_remaining` cap. Source age may be set to at most 300 seconds; the scenario stress floor may be set no lower than 5%, and `aggressive_fraction` no lower than 0.8. The default 0.8 allows a yardage requirement no greater than 125% of scenario expected remaining yardage, subject to the scenario probability stress gate. These are structural screening settings, not optimized betting thresholds. Odds must be observed decimal prices greater than 1; the engine never fabricates them.

Call `Ledger.evaluate_and_record(ticket_id, snapshot, legs, quote=quote)` to evaluate and atomically append the exact inputs, combined quote when available, and decision. Use a new ticket ID for each revised proposal. A quote's `leg_keys` must match all legs exactly, with keys in the form `player_id:market:line`. Later append `settlement` with overall `status` and per-leg results. Resolved legs need observed total, predicted probability, threshold, odds, source evidence, and error category (`opportunity`, `efficiency`, `game_state`, or `construction` as applicable). Keep unresolved results explicitly unresolved. Call `Ledger.verify()` to check the hash chain. Do not edit ledger rows.

For a finished game, call `box, proof = fetch_final_box(game_id)` followed by `settle_recorded_ticket(ledger, ticket_id, box, proof)`. The ESPN player IDs in the frozen legs must match the IDs in the box score. `unclassified` on a losing leg means the numerical miss was observed but its cause has not been independently established.

The engine is deliberately limited to supplied data. When shared samples are absent, the correlation audit fails closed for multi-leg tickets; it does not infer same-play dependence from independent leg estimates. The `approved` field denotes structural research eligibility, **not authorization to bet**. Price comparisons are illustrative until the joint model is calibrated and verified against independent prospective tickets. The [November 2 postmortem](docs/nov-02-postmortem.md) grades the eleven explicit research constructions recovered from the shared chat. No live FanDuel combination quote or complete account betting history is bundled or certified here.

## NFL Quant player terminal

The [team dashboard](docs/team-quant-dashboard.md) adds the 401-slot team
library, shared 2006–2026 play archive, 24 analysis views and descriptive matchup
lab. Add `--team-database <snapshot>/teams.sqlite` to the local terminal command
and open `/teams`. Unavailable charting and unvalidated predictions remain null.

The local player research terminal and 615-metric positional libraries are
documented in [player quant terminal](docs/player-quant-terminal.md). Run
`python -m research.player_terminal --database <snapshot>/players.sqlite` to
open the source-backed interface at `http://127.0.0.1:8766`. Unavailable advanced
metrics and situational splits remain explicitly unavailable. Historical source
coverage is partial; numerical profiles do not establish predictive advantage.

## Tests

`python -m pytest -q`

## Historical research replay

`python -m research.replay_2026 --output <report-directory> --cache <cache-directory>` reconstructs fixed yardage forecasts for 2026 Weeks 1–8 from prior ESPN final boxes. This is a partial, research-only replay. It does not satisfy the live engine's timestamped roster/quote provenance contract and does not measure touchdown, parlay, or betting performance. Keep its output separate from the immutable prospective ledger.

`python -m research.forward_calibrate --output <report-directory> --cache <cache-directory>` fits market-specific probability calibration on 2025 Weeks 5–18, then refits using only prior completed 2026 weeks before scoring each next week. Run the base replay first. The calibration changes probability estimates, not player eligibility or market availability; compare its Brier scores on identical resolved rows before adopting a model change.

The measured correction and its limits are in [2026 weekly replay fixes](docs/2026-weekly-replay-fixes.md). The calibrated single-line estimates are not used as same-game parlay joint probabilities.

`python -m research.parlay_replay --source <calibrated-csv> --output <report-directory>` builds deterministic 3-, 5-, and 8-leg historical research tickets from prediction fields only, saves an outcome-free selection manifest once, then grades their legs. It requires a prior appearance for the same team in the current season, so Week 1 yields no ticket without a historical pregame roster. `python -m research.parlay_report --output <report-directory>` verifies the artifact hashes and writes a ticket-level audit. These reconstructed tickets were not observed or frozen before their historical kickoffs; they do not enter the prospective ledger or imply placed bets, combined prices, or validated parlay probabilities.

`python -m research.complete_parlays --output <report-directory> --cache <cache-directory>` constructs all four research types for every 2026 Week 1–8 game: 3, 5, and 8 yardage legs plus three anytime TD scorers. This is a different, unfiltered baseline: 150+ passing, 20+ rushing, 25+ receiving, ranked by smoothed prior hit frequency without a probability floor. It uses the union of players appearing in the last four prior team boxes, including 2025 for Week 1. Every player's historical kickoff eligibility remains explicitly unverified. TD ranking uses prior rushing/receiving TD occurrence; it does not implement the core engine's red-zone opportunity model. The original screened replay is preserved for comparison; the all-game constructions are not approved wagers or calibrated joint forecasts.

For future game reports, include a dedicated three-scorer anytime TD tier in
addition to the 3/5/8 mixed-market tiers. The [TD report workflow](docs/anytime-td-report-workflow.md)
requires actual scorer markets, game-day eligibility, scenario-specific scoring
opportunity, correlation evidence and an exact combined price before any
betting-readiness claim. `construct_audited_anytime_td_tickets` provides the
evidence-gated research constructor; an unavailable tier stays visible as
`NOT GENERATED` rather than being filled with guesses.

`python -m research.resolve_participation --output <report-directory> --cache <cache-directory>` audits missing legs against both teams' ESPN event-specific rosters. It saves a separate, hash-linked resolution artifact and sources each participant/DNP/roster-absence finding. Invalid selections and nonparticipant tickets remain visible as construction defects; sportsbook void/repricing rules are not inferred. Historical roster lookups are settlement evidence and must not be used as historical pregame inputs.

`python -m research.active_parlays --output <report-directory> --cache <cache-directory>` rebuilds all four ticket types using only players with an explicit `didNotPlay=false` entry on the correct game's team roster. Its output is labeled `POSTGAME_PARTICIPATION_FILTERED_RECONSTRUCTION`, with `valid_pregame_backtest=false`: this deliberately uses hindsight participation and cannot demonstrate a pregame strategy edge. It preserves the original replay. An actual pregame eligibility backtest still requires roster/inactive announcements proven available before kickoff; active but unused players must not be excluded by future participation in that test.

`python -m research.pregame_validation --output <report-directory> --cache <cache-directory>` audits the strict pregame evidence boundary for every game, using a cutoff 30 minutes before kickoff. It rejects postgame participation and future/unversioned source inputs. This is an input gate audit, not a completed backtest; no clean win/loss record is produced when required evidence is missing. Structurally complete bundles still require manual source verification before replay.

`python -m research.collect_pregame_sources --output <report-directory> --cache <cache-directory> --archive <source-archive> --extra-sources <url-list-json>` collects NFL/team inactive, injury, roster and dated transaction source material. The archive keeps original HTML, extracted text and metadata with hashes; the inventory matches inactive sections to all game slots and records failed downloads. Source acquisition does not certify a historical content version or convert postgame records into pregame evidence. `requests` and `beautifulsoup4` are required for the research collector.

`python -m research.source_based_parlays --output <report-directory> --cache <cache-directory>` creates source-informed drafts using explicit initial-roster position lists, dated transactions and game-specific inactive reports. It never filters by final participation. It excludes unverified membership, listed inactives and ambiguous same-day transaction times, and retains blocked slots where there are too few eligible prior-history candidates. These drafts remain source-version-unverified, unpriced and unsettled; the collector has not proved historical content availability. They do not satisfy the strict pregame replay or full-engine approval contract.
# Full rosters, injury roles and mixed-market tickets

Automatic all-team refresh is enabled through the hourly Codex heartbeat
**Update all NFL team profiles**. See `docs/automatic-refresh.md` for cadence,
runtime paths, source-review handling and local-computer requirements. The updater
checks new finals hourly and refreshes team context daily and around games.

Current coaching and scheme context is documented in
`docs/coaching-and-scheme-context.md`. Add `--coaching <reviewed-context.json>`
when building profiles to include all 32 head-coach/coordinator assessments,
calling verification, tactical foundations and coaching-era evidence. New staff
requires re-review; coaching concepts do not invent probability adjustments.

All-team profiles and paired matchup context are documented in
`docs/team-profiles-and-matchups.md`. Run `research.build_team_profiles` to ingest
final-game evidence, preserve source versions, and write 32 team reports. The
matchup API binds both profile versions before rebuilt scenarios can enter the
3/5/8/anytime-TD family constructor. Advanced scheme inputs and actual offered
prices remain required; descriptive team averages do not supply them.

See `docs/depth-chart-and-mixed-market-changes.md`. Current official context is
collected by `python -m research.collect_team_context --output <output> --archive <archive>`.
`research.enrich_team_context` joins all roster/depth profiles and applies only
hash-matching reviewed image charts. Current pages never establish historical
pregame eligibility. `football_engine.roles` handles evidence-backed receiving
role scenarios; `football_engine.construction` allows 3/5/8 mixed-market tickets
through existing engine checks. Passing TD is separate from anytime TD. Exact
offered lines, identity/active-status reconciliation, joint simulations and
combined quotes remain required; no historical tickets were silently replaced.
