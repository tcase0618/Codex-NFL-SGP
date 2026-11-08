# Case NFL Quant Intelligence Platform (`nflquant`)

A deterministic NFL analytics and forecasting system: public data ingestion →
normalized storage → metric libraries → as-of features → walk-forward models →
play-level Monte Carlo → calibrated forecasts → dark institutional terminal.
**No LLM, Claude/Codex or paid inference is used at runtime.**

It sits alongside the existing `football_engine` / `research` ticket-screening
stack and the legacy `terminal/` pages, which are untouched. The new terminal
lives in `terminal/quant/` and is served by `nflquant.api` (FastAPI).

Validated numbers are in [nflquant-validation-report.md](nflquant-validation-report.md).

## 1. Architecture

```
ingest.py      nflverse releases → data/raw/<release>/… + manifest.json (SHA-256, ETag, revisions)
lake.py        → data/lake/plays/{season}.parquet (pbp ⨝ participation ⨝ FTN), games, player_games, injuries…
metrics/       team.py (930-field team library) · player.py (role metric library) · plays.py (per-play indicators)
               profiles.py (windows, context splits, EB shrinkage, percentiles)
ratings.py     as-of opponent-adjusted ridge ratings + Elo benchmark
asof.py        vectorized pregame team-field values (cumulative sums shifted one game)
features.py    pregame game features, projected starting QB, snap-weighted injury load
warehouse.py   SQLite normalized entities (+ entity_status for reserved tables)
models/        game.py (margin/total ladder) · player.py (stat projections) · matchup.py (unit models) · calibration.py
sim/           league.py (fitted behaviour) · engine.py (vectorized play-level MC) · build.py (specs, anchoring) · backtest.py
production.py  fit-on-all → upcoming predictions (training separated from serving)
service.py     read-only query layer;  api.py  FastAPI;  pipeline.py  orchestration
```

### Storage decision
SQLite (`data/warehouse.sqlite`) for normalized entities and model/validation
metadata; Parquet (`data/lake`) for analytical tables (≈1.0M plays, 930-field
team-game matrix, 3.5M player-metric rows). This keeps the existing repo's
SQLite convention, needs no database server, and costs nothing to deploy. The DDL
uses portable types; moving entity tables to PostgreSQL is a connection change.
Redis is not used — the API keeps a small in-process LRU for simulations.

## 2. Data

| Release | Seasons | Content |
|---|---|---|
| `pbp` | 2006–2026 | nflfastR play-by-play incl. EPA, WP, CP/CPOE, xYAC, xpass |
| `pbp_participation` | 2016–2025 | on-field GSIS IDs, personnel, box count, pass rushers, **man/zone, coverage shell, pressure**, time to throw, routes |
| `ftn_charting` | 2022–2026 | play action, RPO, screen, motion, blitzers, drops, contested/catchable, interception-worthy, read thrown |
| `stats_player` | 2006–2026 | weekly box stats |
| `snap_counts` | 2013–2026 | PFR snaps (2012 file is near-empty upstream) |
| `injuries` | 2009–2026 | official reports + practice participation |
| `depth_charts`, `weekly_rosters` | 2006–2026 | 2025+ depth charts are timestamped snapshots |
| `schedules` | 2006–2026 | results, rest, roof, temp/wind, closing lines (benchmark only) |
| `nextgen_stats`, `players`, `combine`, `draft_picks`, stadiums | — | identity, measurements, NGS aggregates, venue coordinates |

* **2026 participation is not published upstream** (404). Coverage-, pressure-,
  personnel- and on-field metrics therefore show `not_covered` for 2026; they
  are never imputed. FTN fields (blitz, play action, drops) remain available.
* Historical franchise codes (SD/OAK/STL/…) map to current keys; originals are kept.
* Ingestion is idempotent: conditional GETs, SHA-256 comparison, retries with
  exponential backoff, `Retry-After` handling, and a revision history per asset.
* **Licensed/proprietary data is not used.** PFF-style grades, blocking win rates,
  individual coverage responsibility, yards after contact, and similar fields are
  listed as `unavailable` in the catalogs with their reason; the `play_charting`
  table schema is reserved for a licensed feed.

### Data tiers
Every API value is one of: **OBSERVED** (source row), **DERIVED** (formula over
observed rows), **MODELED** (ratings, shrinkage), **PREDICTED** (pregame forecast),
**SYNTHETIC** (illustrative; none is served in production views).

## 3. Metric libraries

### Team (930 implemented fields, 20 categories)
`metrics/team.py` defines 77 play-level base metrics plus 11 per-game, 15 drive, 12 special-teams and 4 game-result fields (EPA, success, explosiveness, pressure,
coverage usage, box counts, personnel, PROE, drive, red-zone, special teams…) ×
situational splits (down, red zone, half, score state, two-minute, pressure,
blitz, man/zone, two-high, play action, box, personnel, defensive package,
formation, throw depth) × offense/defense. Each field carries a formula,
population (denominator), unit, direction, source and season coverage, and is
aggregated as Σnumerator/Σdenominator so multi-game windows are exposure weighted.
Game-level context fields (roster health, venue/rest/travel, trends &
regression, opponent strength) are computed in `service.team_context`.

Windows: season, last 3/5/8. Context splits: home/away, dome/outdoor, cold,
windy, short/long rest, divisional, by opponent. Every value shows its sample.
**Shrinkage**: empirical-Bayes beta-binomial (binary) and normal-normal (continuous)
toward the league rate with method-of-moments between-team variance; posterior SD
reported.

Key conventions: EPA/success exclude kneels, spikes and two-point tries;
incompletions count 0 passing yards; sacks are not pressure proxies (a sack on a
participation-charted play is a pressure); unknown charting is `NaN`, never 0;
rush direction is labeled as direction, not run concept.

### Player (132 implemented role metrics + universal A–J framework)
`metrics/player.py`: QB passing (39), rushing incl. scrambles (19), receiving (26),
on-field offense / OL (11), credited defense & penalties (9), on-field defense (12),
kicker/punter/returner (16).
On-field metrics (participation) are **team outcomes while the player is on the
field**, never individual responsibility. Universal framework fields (identity,
usage, production, efficiency, explosive/negative plays, situational,
opponent-adjusted, consistency, rolling 3/5/10, EPA over replacement, standard
error) are role-aware. 19 requested metric families are catalogued as unavailable
with reasons (`/api/q/player/catalog`). Percentiles are within same role and
season with a sample floor.

## 4. Models

All models are trained chronologically; a prediction for season *S* uses only
models fit on seasons < *S* and features computed from games before kickoff.

**Pregame information boundary.** Ratings and team fields use earlier games only;
the projected starting QB is the previous game's primary passer, switched only if
listed Out/Doubtful on the official injury report (never the actual starter);
injury load uses report status × prior snap share; weather is the schedule's
recorded temp/wind (not a verified pregame forecast — limitation). Closing lines
are used only as an evaluation benchmark.

| Model | Method | Target |
|---|---|---|
| Ratings | ridge around regressed prior, recency-weighted, as-of each week | off/def EPA, pass, rush, success, PPD, pressure |
| Game ladder | home-field → Elo → ridge → XGBoost → inverse-MSE ensemble | margin, total |
| Win probability | Normal(μ, σ_OOS) then Platt recalibration on prior OOS seasons | P(home win) |
| Player projections | XGBoost Poisson (counts) / squared error (yards) + multi-quantile; NB dispersion for count intervals | 28 stats incl. defenders & specialists |
| Matchup | play-level XGBoost on as-of unit profiles; impact = vs league-average defense | dropback EPA/success/pressure/sack/explosive/INT; run EPA/success/stuff/explosive |
| Simulation | vectorized play-level Monte Carlo, anchored to the game model | full game & player box scores |

Explanations: TreeSHAP via XGBoost `pred_contribs` plus standardized ridge terms.
**AutoGluon** was evaluated but is not installed by default (≈2 GB torch
dependency for a 5k-row game table); the ladder already includes a gradient-boosted
benchmark. Hierarchical modeling is empirical Bayes (normal-normal / beta-binomial
and ridge priors), not full MCMC.

## 5. Simulation

`sim/engine.py` advances all N simulations one snap per step with NumPy masks.
It models kickoffs (empirical start), situational dropback choice (fitted
logistic + team PROE offset), sacks, scrambles, target choice by projected share,
completion/INT with receiver catch offsets, yards from empirical distributions
scaled by team/defense/receiver multipliers, fumbles, penalties (incl. pre-snap
fouls; half-the-distance), first downs, TD/XP/2-pt, FG (distance logistic), punts
(empirical net by field position), 4th-down go/FG/punt (fitted multinomial),
turnover on downs, safeties, clock runoff by outcome and hurry-up state, kneel-outs,
halftime possession, and overtime by season rules (sudden death → modified
sudden death 2010/2012 → both-possess 2022 playoffs / 2025 regular season; 10-min
regular-season OT from 2017; ties allowed in the regular season).

League behaviour is refit from the three prior seasons (+ completed weeks of the
current season). Per-offense efficiency scalars are iterated until simulated mean
points match the game model, so expected score, score distribution, win
probability and every player's box score come from one set of simulations. Three
identities are checked in every simulation: Σ receiving yards = team passing yards,
Σ rushing yards = team rushing yards, and the points ledger.

Settings: 1,000 / 10,000 / 50,000 simulations (seeded, reproducible). Measured:
10,000 sims including anchoring ≈ 7 s on 4 cores. Timeouts, onside kicks,
two-point optimisation and in-game injuries are not modeled explicitly.

## 6. Running it

```bash
pip install -r requirements-quant.txt
python -m nflquant.pipeline                 # full build (~1–2 h on 4 cores; player walk-forward dominates)
python -m nflquant.pipeline --refresh       # scheduled refresh (conditional ingest → production)
uvicorn nflquant.api:app --host 127.0.0.1 --port 8780   # open http://127.0.0.1:8780
python -m pytest -q tests/test_nflquant_*.py
```

Environment: `NFLQ_DATA_DIR` (default `./data`), `NFLQ_FIRST_SEASON`,
`NFLQ_LAST_SEASON`, `NFLQ_HTTP_TIMEOUT`, `NFLQ_MAX_RETRIES`. No credentials are
required for public sources. `deploy/Dockerfile` builds a serving image (mount
the built data at `/data`); `deploy/crontab.example` schedules refreshes.

## 7. API

| Endpoint | Purpose |
|---|---|
| `GET /api/q/status` | sources, coverage, entity status, production cutoff |
| `GET /api/q/teams`, `/team/catalog`, `/team/profile`, `/team/trend` | team library |
| `GET /api/q/players`, `/player/catalog`, `/player/profile` | player library |
| `GET /api/q/schedule`, `/game/prediction`, `/game/simulate`, `/game/matchup` | forecasts, simulation, matchup lab |
| `GET /api/q/audit` | validation results and model versions |

OpenAPI docs at `/api/docs`. All endpoints are read-only.
