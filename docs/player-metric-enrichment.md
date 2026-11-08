# Public player metric enrichment

`research.enrich_player_metrics.run(base, team_database, output)` creates a new directory and SQLite-backup snapshot. Both input connections are read-only. The CLI takes `--base`, `--team-database`, and `--output`. It refuses an existing destination and compares both original database hashes before and after building. It does not change runtime configuration, history, automation, execution gates or server defaults.

## Source and schema diagnosis

The active v4 has weekly stats in 2006–2018 and 2020–2024. It has no 2019 weekly stats. 2025 and 2026 stats are partial ESPN final-box supplements (1,282 and 2,359 rows respectively). NGS coverage is 2016–2023 plus only eight rows in 2024 (two passing, four receiving, two rushing); 2025 and 2026 NGS coverage is absent. Existing historical field names match the implemented adapters: the 2026 gaps are not safely repairable by renaming box-score fields. `coverage.json` records exact kind/season counts and observed numeric field counts from the database.

The public play source is the team's already archived nflverse/nflfastR play-by-play. The output copies and verifies each source's original raw compressed bytes. `lineage.json` supplies original URLs, SHA-256 hashes, original collection times, new adapter observation time, archived paths and grain. Primary definitions are [nflfastR play fields](https://nflfastr.com/articles/field_descriptions.html) and [player-stat variables](https://nflfastr.com/articles/stats_variables.html); archived definition pages and their hashes are listed separately. Definition downloads can fail without inventing their contents; status is explicit.

## Data contract for integration

The 615-entry position catalog remains unchanged. `football_engine.player_metrics.PLAY_METRICS` maps supported existing metric IDs to explicit event, measurement, operation, threshold, scale and optional situation. `play_definition(metric_id)` supplies versioned formulas and eligibility notes. No extra fields are silently added to the catalog.

The v5 `players.sqlite` preserves all v4 rows and appends observations with kind `pbp_season`, `pbp_home`, `pbp_away`, `pbp_first_half`, `pbp_second_half`, or `pbp_third_down`. Existing primary-key grain remains stable GSIS player ID / season / week / REG / kind. The JSON includes `game_id`, `game_date`, team, home/away, definition revision, and `<metric_id>:n` and `<metric_id>:d` numeric fields. Every metric has its own observed numerator and denominator. `event-trace.jsonl.gz` maps these contributions to source ID, game ID and play ID; original raw compressed sources preserve the underlying bytes.

`PlayerProfileStore(path).metric(...)` and `.profile(...)` keep their public signatures. Existing imported charting and weekly-stat calculations retain precedence. Play-derived observations are used when the old adapter has no usable value. Weekly and play denominators are never spliced. Results include the actual formula, validated adapter definition, sample opportunities, source IDs, observed weeks and source-vintage checks. Career is bounded by the selected season. Last-three/five windows anchor on the union of actual observed games rather than the last games with a qualifying metric. Half splits exclude overtime; third down means `down=3`. Pressure, blitz, man/zone, adjustment and other unsupported splits remain unavailable. No precision or confidence calibration is implied.

The overview's `metadata.summary` retains prior properties and adds `covered_seasons`, `source_years`, `latest_covered_season`, `derived_rows`, `player_metric_enrichment`, and `source_definition_links`. `metadata.player_metric_enrichment_v1` supplies the full detailed report. `player_event_metric_definitions` stores exact formulas. `coverage.json` reports before/after populated catalog slots for each season and latest canonical player position, using the same position convention as the existing profile API and the union of original and derived player-seasons.

## Eligibility and limitations

All joins use explicit stable GSIS IDs. Unknown GSIS IDs are counted and quarantined, with no name matching. Metrics use attributed passer dropbacks, rusher attempts or targeted receiver events. Explicit scramble rusher IDs can identify dropbacks without an inferred passer name. A player/play is counted only once in the union for QB EPA/play. Kneels, spikes, no-play rows, special teams and two-point attempts do not contribute. Measurement-specific denominators exclude absent or nonfinite fields. A genuine measured zero remains zero; no opportunities or no measurements stays null. Existing weekly additive zero behavior is preserved, so a populated slot is not a participation or applicability claim.

Success uses the public `success` field (EPA-positive event indicator). Explosive passes/receptions require at least 20 yards; explosive runs require at least 10 yards. EPA fields use full play EPA over the stated eligible event set. Receiver EPA is full play EPA on targeted plays, never isolated receiver contribution. Red zone is the opponent 20 or closer, two-minute situations are the last 120 seconds of either half, and fourth-quarter close games have an absolute pre-play margin of eight or less. Short-yardage conversion uses the reported first-down rushing indicator on attempts with at most two yards to gain, not inferred blocking quality. Each source game must be an archived scheduled final admitted by the existing team snapshot; missing games are coverage gaps.

No route counts, pressure, proprietary grades, blocking or coverage assignments, snap counts, full participation, activity or availability are inferred. OL and DB supplemental snap metrics require a separate actual identity-mapped source; this adapter does not fabricate them. Current-vintage historical events are not certified historical pregame knowledge. No betting permission or matchup probability is produced.

## Validation

`python -m pytest -q tests/test_player_metric_enrichment.py tests/test_player_metrics.py tests/test_charted_player_metrics.py`

The enrichment tests cover immutable input hashes, backup lineage, archive hashes, safe destination refusal, source cutoffs, separate EPA denominators, measured zero, missing fields, threshold boundaries, sack/scramble attribution and de-duplication, exclusions, situational bounds, actual last-N windows and unsupported grades/splits. Live snapshot integrity and exact coverage are persisted under the task folder's `outputs/metric-depth-expansion/player/v5`.


## Audit corrections AUD010 / AUD011

WR Touchdown / target is a percentage in both the existing weekly-stat and PBP adapters: 100 � touchdowns / measured targets. One touchdown in ten targets is 10%, with unit `%`. Unscaled persisted `:n`/`:d` observations remain unchanged; the runtime scale, catalog unit and persisted formula definitions were corrected and artifact hashes regenerated. Empty metadata summaries use `setdefault` to create the observations counter mapping. Focused validation: 35 tests passed, including both regression cases. See task output `player/v5/audit-corrections.json` for measured live impact.


## Read-only supplemental charting context

Configure `PlayerProfileStore(player_path, charting_path=charting_path)` and call `supplemental_context(player_id, season=2026, as_of=None, week_max=None)`. This method does not change the advanced metric catalog or either SQLite snapshot. The parent owns server/UI wiring.

The returned keys are `schema`, `player_id`, `season`, `status`, `snaps`, `snap_totals`, `ngs`, `sources`, `scope`, `limits`, and `reason`. `status` is `OBSERVED_PUBLIC_CONTEXT` or `UNAVAILABLE`. Snap rows carry `game_id`, `pfr_id`, `season`, `week`, `team`, `offense_snaps`, `defense_snaps`, `st_snaps`, `total_snaps`, and `source_ids`. `snap_totals` maps each count field to `{value, observed_rows, missing_rows}`. Total snaps sum the three observed categories only when all three exist; missing counts remain null and genuine zero is retained. A total with missing rows covers only its explicit observed rows. Offense, defense and special-teams appearances are participation counts, never inferred blocking or coverage snaps.

Identity requires the snap row's explicit GSIS ID and the PFR-to-GSIS crosswalk to agree; its crosswalk source ID must also match. Snap source and crosswalk source must both exist and pass `as_of`. Only exact-season REG rows contribute. No name join, historical fallback or inference of availability is used.

NGS rows have `kind`, `season`, `season_type`, `week`, `team`, `grain`, `fields`, `raw_fields`, and `source_ids`. `fields` retains selected finite numeric public provider measurements and opportunity fields, with missing values null. `raw_fields` preserves the source row's field names/values without relabeling their definition. `grain` is `provider_season_aggregate` for week 0 or `player_week_team` otherwise. These are source rows, not a locally combined estimate: week 0 is never added to weekly values. A partial `week_max` omits season aggregates. `as_of` filters NGS source vintage, and a mismatched raw GSIS ID is withheld. `sources` resolves every returned source ID to its URL, hash, collection time, raw path, attribution and provider terms.

The method's scope is exact-season source context; profile split selection does not silently reinterpret it as split-specific participation. The focused tests now total 37 passed, including defensive zero counts, missing totals, identity conflicts, postseason/year exclusion, source/crosswalk cutoff, NGS aggregate separation and unchanged database hashes.
