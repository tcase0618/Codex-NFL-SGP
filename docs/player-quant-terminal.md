# NFL Quant player terminal

Implemented November 3, 2026 in this football repository. This is a local,
read-only research interface backed by source-versioned player observations.
It does not create wagers, update a sportsbook, or produce calibrated forecasts.

## Positional libraries

615 metric slots across 18 role libraries: QB 65; RB 40; WR 45; TE 35;
OT/OG/C 40 each; EDGE 35; DI 35; LB 40; CB 45; S 35; and K/P/LS/KR/PR/FB
20 each. All specifically enumerated user metrics are retained. Specialist
libraries extend the supplied examples with explicit field names.

Every slot includes a stable metric ID, role, category, unit, denominator
contract, context conventions, source requirement and definition-validation
status. Unsupported charting definitions are drafts requiring adapter validation,
not production-certified provider definitions. Supported calculations expose
their exact source-field formula and missingness policy. No overall player
rating or proprietary grade is synthesized.

The initial public adapters implement 78 role/metric calculation slots across
the libraries; a slot is populated only when that player's actual observations
exist. The remaining slots are not numerical coverage claims. Any catalog field
can additionally receive explicitly sourced charting through
`research.import_charted_player_metrics`, which creates a new database snapshot.
It requires stable player/metric IDs, a provider and HTTPS reference, exact
definition/method/unit, a completed coverage period, positive sample sizes and
finite values. The archived import is hashed. Values are labeled provider
supplied, not independently calibrated. There is no HTTP write endpoint.

Eighteen views are represented: season, last 3, last 5, available career,
blitz, pressure, clean pocket, man, zone, home, away, first half, second half,
third down, opponent adjusted, league percentile, year over year, and sample /
confidence. Unsupported splits explain which play-level inputs are missing.
They do not reuse the season average as a situational statistic.

## Real data and limits

The first import archived 46 public nflverse assets. The final snapshot has
10,014 stable GSIS identities and 328,512 player-week/source observations:
306,061 public weekly-stat rows, 18,810 qualified NGS rows and 3,641 ESPN
supplement rows. There are 2,595 latest weekly roster profiles; this is not
the number of confirmed game-day active players.

Requested history starts in 2006. The checked weekly release has gaps for
2019, 2025 and 2026; ESPN supplies only partial 2025–26 coverage. Tracking
begins in 2016 and can have qualifying-player, week and season gaps. Availability
of a file does not establish full-season completeness. Role-specific charting,
pressure attribution, route assignments, grades, throw velocity, blocker win
rates and calibrated opponent adjustments remain unavailable unless sourced.

Raw CSV/gzip files, release metadata and content hashes are preserved. The
database retains each row's source and collection time. Metrics do not treat
absent columns or zero denominators as zero production. Current-vintage history
cannot certify pregame knowledge. As-of queries reject observations collected
after the requested knowledge cutoff; available-career calculations stop at
the selected season. Recent tracking windows are anchored to observed game
rows rather than silently taking the last N non-missing tracking rows.

One GSIS identity (Chris Manhertz) has conflicting public ESPN aliases. Its
GSIS history and separately sourced weekly membership are retained, while its
ESPN alias is cleared and ESPN statistics are quarantined pending reconciliation.
No identity is joined by name. Ambiguous generic OL/DL/DB role mappings are
explicitly reviewable; returner libraries are separate lenses for actual
return-stat observations.

Rates use summed numerators / summed denominators. Tracking means are
opportunity weighted. Year-over-year values use common observed week numbers
in adjacent seasons and are not opponent adjusted. Percentiles are empirical
midranks of raw values among observed same-season, same-position peers, without
an opportunity floor; higher is not automatically better. Sample counts are
reported, but confidence intervals and validated minimum samples are not
invented. Sacks are not used as a pressure-rate proxy.

## Interface

The responsive terminal includes player/team/position search, all positional
categories, observed-only and metric-search filters, exact metric inspection,
18-view availability, historical charts with breaks for missing seasons,
same-role comparisons, JSON profile export, source URLs/hashes and a clear
matchup-input panel. That panel stays unavailable without the full forecast
inputs. Current weekly team context is separated from historical recorded teams.

Existing `team_candidates` profiles now carry metric-library references. An
export attaches the role libraries to all 2,536 official-source roster/depth
profiles for all 32 teams, without making name-only joins to numerical data.
Future existing roster refreshes inherit the schema references automatically.
The numerical terminal snapshot is a separate import; this change does not
claim automatic advanced-data refresh after every game.

## Run / rebuild

Run `python -m research.collect_player_terminal --output <new-source-directory>`
to archive available public sources. Run `python -m research.build_player_terminal
--root <task-root> --archive <source-directory> --output <new-snapshot-directory>`
to build a new snapshot without overwriting prior versions. Run
`python -m research.player_terminal --database <snapshot>/players.sqlite --port 8766`.
The server binds only to 127.0.0.1 and exposes read-only profile endpoints. It
does not serve arbitrary filesystem paths or the SQLite file.

The task output includes an `Open-Player-Terminal.ps1` launcher for this Windows
workspace. It runs the local app while its console remains open. Source data is
not published externally.

Validation includes the full existing regression suite, metric-count and
identity checks, weighted denominators, unavailable/zero distinction, source
knowledge cutoffs, career boundaries, recent tracking-window anchoring, matched
year-over-year periods, charted-import validation/immutability, and browser checks
for filtering, trends and comparisons. The completed full suite has 105 passing
tests. Design review: 8.5/10; small-text contrast and additional play-level
visualizations remain improvements, with the latter requiring real source data.

Public reference documentation:
- https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html
- https://nflreadr.nflverse.com/reference/load_player_stats.html
- https://nflreadr.nflverse.com/reference/load_nextgen_stats.html
