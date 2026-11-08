# Public charting research overlay

This module reads a separate derived SQLite snapshot. It does not update the team
or player base stores, betting gates, runtime configuration, history, or automation.
The parent owns server/UI integration and all changes to team_quant.py.

## Parent reader contract

```python
from football_engine.charting_metrics import (
    ChartingMetrics, team_charting_metrics,
    team_charting_sources, team_charting_metadata,
)
reader = ChartingMetrics(charting_path)
reader.verify_base(base_path)  # True on exact SHA256; ValueError on mismatch
reader.value(team, season, metric_id, view='season', as_of=None)
reader.value(team, season, metric_id, week_max=8)
team_charting_metrics(charting_path, team, season, week_max=None,
                      as_of=None, view='season')
team_charting_sources(charting_path)
team_charting_metadata(charting_path)
reader.source(source_id)
reader.evidence(team, season, metric_id, view='season', as_of=None, limit=80)
```

`value` returns None for unsupported metric IDs/views. Supported requests without
observations return a standard value dict with value=None/status=UNAVAILABLE.
Other keys include sample_plays, sample_opportunities, sample_games, source_ids,
missing_opportunities, inapplicable_opportunities, filter_missing_opportunities,
eligible_observations, numerator, qualified, complete_fields, formula, definition,
reason, unit, season, view, observed_weeks, _game_values, feature_signature,
confidence_interval, percentile, and historically_pregame_certified=False.
The all-metrics helper returns metric ID -> dict, and the supported ID list is
metadata.supported_metric_ids. Use the reader instance for cached repeated reads.

Supported views are season, last3, last5, last8, home, away, man, zone, blitz,
four_man. Last-N windows are selected from base PBP games before filtering charted
rows, so missing charting does not expand a window. Other analytical views return
None; never substitute the season value. The parent must call the overlay before
a hardcoded charting-view rejection. Evidence currently provides the exact
aggregate and its sources, rows=[], total=sample_plays and
row_evidence_available=False. It must not fall back to unrelated base row evidence.

Source IDs are strings prefixed `charting:` (base dependencies use
`charting:base:<sha256>`); all returned source IDs resolve through sources(). The
parent should preserve this namespace and merge these source records into profile
and inspector responses. `sources` columns are id TEXT PRIMARY KEY, reference,
sha256, observed_at, path, kind, attribution, license. No numeric base ID reuse.

`metadata` is key TEXT PRIMARY KEY, value JSON TEXT. Important keys:
base_input_path, base_input_sha256, base_input_sha256_after, base_unchanged,
schema_version, observed_at, requested_seasons, covered_seasons,
dataset_availability, release_lag, supported_metric_ids, supported_views,
source_id_namespace, historically_pregame_certified, ngs_grain, snap_grain.

## Primary sources, timing and licensing

* [FTN charting documentation](https://nflreadr.nflverse.com/reference/load_ftn_charting.html)
  describes a manually charted subset, normally charted within 48 hours, licensed
  CC-BY-SA 4.0 with attribution to **FTN Data via nflverse**. The actual 2026
  release contains 20,572 rows across 121 games through week 8.
* [Participation documentation](https://nflreadr.nflverse.com/reference/load_participation.html)
  says 2023+ FTN participation is released after all postseason games. 2024 and
  2025 release assets exist; the 2026 participation asset is absent as expected.
  Explicit man/zone, coverage labels and personnel are historical only here.
  The FTN data are CC-BY-SA 4.0 with attribution to FTN Data via nflverse.
* [Snap count documentation](https://nflreadr.nflverse.com/reference/load_snap_counts.html)
  identifies Pro Football Reference game-level counts. Actual 2026 coverage is
  11,292 player/team/game rows across 121 games through week 8. `players.csv`
  provides an exact PFR-ID-to-GSIS/ESPN crosswalk; 11,272 of those rows match.
  Missing crosswalks remain NULL. No name-based identity join is used.
* [NGS documentation](https://nflreadr.nflverse.com/reference/load_nextgen_stats.html)
  describes minimum-attempt-selected player weekly statistics, with week=0
  representing season summaries. The archived aggregate files contain actual
  2026 passing/receiving/rushing rows through week 8 (279/681/286 rows including
  summaries). These are separate player observations, not play-level charting.

FTN license documentation was verified. A dataset-specific redistribution license
for the snap counts, NGS and player crosswalk was not verified; their sources
explicitly retain provider terms and do not invent a permissive license. The
upstream nflverse-data root LICENSE URL returned 404; package licensing is not
treated as a license grant for those provider datasets. No paid data or login was
used. Raw bytes, release API responses, documentation and SHA256 hashes are
archived. The initial archive contains 139,960,846 asset bytes; optional duplicate
Parquet equivalents were retained but only CSV equivalents are parsed.

## Grain and field definitions

`plays` is keyed by exact nflverse game_id/play_id, with season, week, game_date,
posteam, defteam, pbp JSON, game JSON, base_source_ids JSON and fields JSON. Each
field has a value and its own source_ids. PBP is used for exact team attribution,
scrimmage/dropback eligibility and EPA, never to infer coverage shells. Source
charting rows with no exact game/play/team match remain in raw_records and are
excluded from calculations. All 14,028 quarantined rows are historical 2024/2025;
the current 2026 charting release joins without those failures. Full source
seasons exceed the games represented in the base store; coverage reflects that
base intersection rather than claiming every archived historical game is usable.

`raw_records` preserves source_id,row_number,kind,season,game_id,play_id,team,data
at source row grain. `player_snaps` has game_id/team/pfr_id primary key, gsis_id,
season,week,offense_snaps,defense_snaps,st_snaps,source_id,crosswalk_source_id,data.
`player_crosswalk` has pfr_id primary key,gsis_id,espn_id,source_id,data.
`ngs_observations` has kind/season/season_type/week/gsis_id/team primary key plus
source_id,data. Week=0 summaries must never be pooled with weekly rows.

* Eligibility excludes special teams, no-plays, kneels, spikes and two-point tries.
  Dropbacks require explicit qb_dropback=1, including sacks and scrambles.
* Blitz frequency is explicit FTN n_blitzers>0 on eligible dropbacks, not a
  five-rusher proxy. Four-man filtering requires explicit n_pass_rushers=4.
  Counts outside 0..11 are missing/invalid. The 2026 source has one row reporting
  56 rushers / 52 blitzers; these are excluded, never clipped to plausible counts.
* FTN is_motion means motion before OR at the snap. It supports a combined
  coaching.motion_tendency and motion-play EPA. Neither motion-at-snap nor a
  pre-snap-only rate is supplied. Play-action frequency is the explicit
  play-action flag on dropbacks; RPO frequency is the explicit flag on scrimmage.
* Coverage rates use explicit MAN_COVERAGE/ZONE_COVERAGE and COVER_n labels.
  COVER_2 excludes 2_MAN. No single-/two-high shell, disguise, simulated pressure,
  coverage bust or run-concept inference is supplied.
* Personnel uses explicit position counts totaling 11; FB counts as an RB for
  11/12/21/13 grouping, and all of RB/TE/WR counts must match. Nickel/dime counts
  explicit DB/CB/S/SS/FS position counts of five/six. These are personnel labels,
  not coverage assignments. Unparseable personnel is missing.
* Conditional EPA uses only plays satisfying the explicit field. No subgroup
  plays means INAPPLICABLE, not zero EPA. Frequency zero with a valid nonempty
  denominator remains a genuine numeric zero.
* No OL pass-block snaps, DB coverage snaps, individual grades, win rates,
  availability or proprietary contact metrics are synthesized from snap totals.
  Supplemental exact snap fields exist outside the 401-metric team catalog.

## Missingness and knowledge cutoff

Every field's source observation time and every PBP/game dependency must be <=
as_of. An archived present-day historical row cannot become a historical pregame
observation merely because its game date is old. The overlay is never certified
as historically pregame. `verify_base` checks the exact SHA256 once at startup.
The builder hashes the base before and after reading with SQLite mode=ro.

Rates pool valid observed plays and report missing/invalid eligible observations
separately. Incomplete fields withhold qualification even when a descriptive
value can be calculated. The screening minimum is 100 observed plays and 3 games,
not a validated betting rule. Filter missingness is separate for man/zone/blitz/
four-man views. Source and base play coverage can still be partial; numerical
population is not proof that a season, provider feed or betting model is complete.
EPA remains a provider-model-dependent measure with unverified training vintage.

## Build, tests and persisted verification

Run `python -m research.public_charting --base <base.sqlite> --archive <archive>
--output <NEW-overlay.sqlite>`. Add --collect only with a new archive directory.
No existing overlay is overwritten. The recorded initial discovery used at most
19 requests (including four web documentation reads) and stayed below 250 MB of
downloaded assets. Rebuilds use the archived bytes and do not require network.
Stable keys, sorted serialization and pooled calculations are deterministic;
build observation timestamps mean byte-identical SQLite files are not promised.

Run `python -m pytest tests/test_public_charting.py -q`. The 13 regression checks
cover zeros vs missingness, personnel parsing, special-teams eligibility, invalid
counts, every source cutoff dependency, last-N/week filters, historical seasons,
unsupported metrics/views, base mismatch refusal, exact aggregate evidence and
prohibition on overwriting an existing/base database.

`charting-build-report.json` retains per-source field completeness, raw counts,
join failures, fingerprints and crosswalk coverage. `live-coverage.json` compares
the exact base with the overlay for all 32 teams on the 45 supported metric IDs.
The comparison is an adapter coverage audit, not a claim that all 401 slots or
licensed data gaps are filled. Parent server/UI deployment is separately owned.
