# Supplied readiness evidence

`research.readiness_evidence` bridges an existing `PREGAME_CAPTURE_V1` capture
and genuinely supplied role artifacts into the existing accuracy evidence
manifest. It performs no network access, model fitting, wager recording,
backdating, or runtime/config/state writes. Existing report directories are never
overwritten. Missing evidence remains missing; no placeholder artifacts are made.

## Public API

`run(capture_path, output, *, inventory_path=None, evidence_path=None,
as_of=None, expected_capture_sha256=None)` returns `status`, `manifest_path`,
`report_path`, `scope`, `missing_roles`, `reasons`, and `audits`. `output` must be
a new directory outside the capture. The parent passes `manifest_path` to
`accuracy_swarm.run` for all seven independent auditors. The assembler does not
replace that audit or turn a source capture into an engine snapshot.

The CLI uses the actual current UTC clock:

```text
python -m research.readiness_evidence --capture <existing-capture-directory> --evidence <existing-evidence-manifest> --inventory <supplied-role-inventory> --output <new-report-directory>
```

Omit `--inventory` when none exists. This produces a useful blocked report with
explicit absent roles. BLOCKED returns exit code 2. `verify_pack(output,
expected_seal_sha256=None)` rechecks the closed output inventory and linked input
hashes. An externally retained seal digest is stronger than a self-supplied seal.
Neither seal establishes source authenticity.

`assess_history(workspace, *, as_of=None)` inspects the bounded existing task
outputs: one-level evidence manifests and replay prediction CSVs. It actually
reruns the existing calibration/backtest contracts. It never interprets week
ordering or current-vintage reconstruction as frozen prospective validation.
Candidate consistency is not historical authenticity or model adequacy.

## Genuine role inventory

The JSON inventory is a list of caller records using `role`, absolute `path`,
raw-byte `sha256`, source `reference`, exact `game_id`, and aware `published_at`,
`available_at`, `observed_at`. Non-ledger times must be ordered, no later than the
assessment clock and captured decision cutoff. Capture caller records are read
directly from the existing closed capture; extra roles can be supplied in this
inventory without recapturing or editing the old bundle. Duplicate roles block.

Roles are `model`, `model_inputs`, `forecast`, `opportunity`, `offered_quote`, and
`ledger`. Historical ledger inventory is not required to name the future game;
it needs an actual checkpointed SQLite file, hash, and source reference. Its
settled-history semantics remain the existing ledger auditor's responsibility.
The assembler does not generate ledger observations or settle an upcoming game.

- `model` is the existing calibration model block: `artifact`, canonical
  `sha256`, `fitted_at`, `training_sha256`, `frozen_sha256`. Its artifact carries
  a real model version and explicit capabilities, never an invented fit date.
- `model_inputs` is the existing calibration inputs block: `artifact`, canonical
  `sha256`, `available_at`, `frozen_sha256`. The input artifact binds `game_id`,
  `team_profile_versions`, and `player_ids` for every selected leg.
- `forecast` uses the existing archived forecast ticket shape: exact `game_id`,
  `ticket_id`, `leg_ids`, `legs` (each `leg_id`, `player_id`, `team_id`, `market`,
  `line`), `probabilities` by leg ID, `observed_at`, `model_sha256`,
  `inputs_sha256`, and `team_profile_versions`. Each probability must match the
  engine forecast for that exact leg. Swapped leg IDs do not preserve clearance.
- `opportunity` is the actual `snapshot_evidence` JSON: `snapshot`, `legs`, exact
  combined `quote`, and `model_sha256`. The bridge also requires `inputs_sha256`
  and `forecast_sha256` (canonical digest of the complete supplied forecast) to
  freeze the source-version bindings. These must be supplied, not inferred.
- `offered_quote` must exactly equal the opportunity's combined quote object.
  Quote freshness and precise leg keys remain validated by the original engine.

Raw file SHA-256 and canonical JSON SHA-256 serve different purposes; the tool
never substitutes one for the other. Genuine supplied bytes are copied unchanged
into `artifacts/`. Existing derived metric, historical source, selection, and
settlement references retain their absolute paths and are rehashed; frozen
selection JSON is never rewritten to invent a new archive history.

Existing `evidence_path` sections are preserved, including `derived_metrics`,
`calibration`, `backtest`, `ledger`, `opportunity_snapshot`, and scope. Conflicting
game/profile/model/quote evidence blocks. Passing historical checks are further
bound through `research.evidence_scope.bind_game_evidence`. Applicability comes
from `validation_evidence` inside the canonical hashed model artifact, including
full semantic backtest-manifest digest, historical-ledger byte digest, and market
coverage. Mutable `applicability` labels have no authority. Historical cohorts
retain their actual historical game IDs; they do not need the future game ID.

## Canonical market capability policy

`metric_audit(..., *, capability_evidence=None, scope=None)` is wired into the
canonical swarm game path. Full all-team/default calls retain the original
lineup/charting requirements. Missing or invalid scoped declarations block.

The declaration must be exactly `calibration.model.artifact.capabilities` inside
the selected canonical hash-validated model. It uses schema
`MODEL_CAPABILITIES_V1`, explicit `markets`, and `features`. Supported feature
names are `volume`, `yardage_efficiency`, `touchdown_opportunity`,
`personnel_interactions`, `pressure`, `coverage`, `concept`; unknown or empty
declarations block. Actual requested markets come from the hash-verified
opportunity legs, not caller-provided market labels. A declaration contains no
copy of its enclosing model hash, avoiding a circular digest requirement.

The canonical runner builds `capability_evidence` from existing calibration model,
opportunity archive, and `metric_capabilities.coverage`. Only explicitly unused
lineup/charting features can omit those requirements. Interaction features need
lineups; pressure/coverage/concept features need charting, regardless of database
row counts. All canonical metric source/code/derivation gates remain in place.

External coverage records are keyed `lineups`/`charting`, each with absolute
`path` and raw `sha256`. Coverage JSON binds `kind`, `game_id`, `model_sha256`,
`team_profile_versions`, `observed_at`, `kickoff`, and nonempty `rows` for both
selected teams. Each row binds `game_id`, `team_id`, and `source` with absolute
`archive_file`, `sha256`, `reference`, `observed_at`. Its exact non-source fields
must exist in that source archive's `rows`; source root `kind`, `game_id` and
`observed_at` must match. Lineup rows require eleven distinct player IDs;
charting rows require all declared charting feature names. Unrelated archives,
unrelated rows, altered model declarations, and uncovered teams block.

## Assessment boundaries

The report can become `EVIDENCE_CONSISTENT` only if its role, binding, capability,
and original evidence checks pass. `betting_ready` stays false. Sample sufficiency
and performance approval remain `NOT_ASSESSED`; historical authenticity is not
established by local hashes. Synthetic regression fixtures are software tests,
never reported as actual validation or evidence of predictive skill.

The actual live capture used during this repair has no supplied role artifacts.
All six remain unavailable. The available reconstructed prediction CSV does not
have frozen model/input/forecast-time/baseline provenance. No no-leak prospective
walk-forward performance result can be claimed from those inputs.
