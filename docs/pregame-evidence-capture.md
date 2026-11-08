# Forward pregame evidence capture

This CLI creates a new research archive from **current retrievals**, rather than
repairing historical provenance. It never makes a forecast, price, ticket, wager,
settlement, performance result, model version or prospective backtest label.
It does not change the engine, runtime state, configuration or automation.

Run from the repository root with Python 3.11+ and the existing `requests`
dependency:

```powershell
python -m research.pregame_capture capture --output "C:\research-evidence\new-capture"
```

The destination must not exist. Every rerun requires a new destination. There is
no `--as-of`, kickoff override, cached-source fallback, overwrite or resume mode.
The CLI records timezone-aware system UTC before/after every request and caller
file read, and when the complete evidence set has been frozen. Tests inject a
clock only through the Python API. An invalid or backwards clock aborts capture;
an interrupted directory without a complete manifest and seal is unusable.
System time is recorded evidence, not an independently attested timestamp.

## Public retrieval and game selection

The tool fetches ESPN's public NFL daily scoreboard for UTC yesterday through
seven days ahead, stopping at the first day containing a future scheduled game.
It checks the NFL league label, explicit `pre` status, aware kickoff, event and
competition identity, and both team IDs. Candidate games are sorted by kickoff.
Each attempted day is archived, including empty schedules and error responses.
If an earlier day's retrieval fails, any later candidate is preserved but the
`next_game_selection_complete` flag stays false. The window is bounded; no
historical search runs. The daily endpoint is used because ESPN's range request
can return HTTP 400.

The selected game's public ESPN summary is fetched and its event/competition ID,
kickoff, team sides and scheduled status must match. This freezes current
context; summary injuries, roster listings or team odds do **not** automatically
establish starters, a complete active universe, player prop prices or a combined
offered quote. A game that starts during capture or whose capture finishes after
the kickoff-minus-30-minute decision cutoff receives an explicit blocker.
No late artifact is backdated or relabeled as pregame.

Only the fixed public HTTPS ESPN endpoints are fetched. Requests verify TLS using
the normal requests trust store, disable redirects, request identity encoding,
and record URLs, status and response headers. `transport_verified` means HTTP
200 at the exact requested HTTPS URL; it is distinct from content validation and
does not establish bookmaker authority. Error bodies are archived too. `.body`
files preserve the exact response-body bytes delivered by requests, without
JSON reserialization; HTTP framing and TLS packets are not captured. No paid API,
API key, browser login, sportsbook action or order endpoint is involved.

## Optional genuine caller artifacts

Pass an existing JSON inventory with `--artifacts "C:\evidence\artifacts.json"`.
Each list entry describes one existing file; relative paths resolve beside the
inventory. Supported roles are `offered_quote`, `forecast`, `model`,
`model_inputs`, `starter_proof`, `active_proof`, and supplementary `context`.
Multiple files per role are allowed. Include every underlying model/input file
explicitly; the tool does not follow artifact references or manufacture missing
files. Any binary format can be frozen.

Every entry should contain:

| Field | Meaning |
|---|---|
| `role`, `path` | One supported evidence role and the actual existing artifact file |
| `sha256` | SHA256 of that file's exact raw bytes, independently supplied by the caller |
| `game_id` | The selected ESPN event ID, as a string |
| `reference` | Real source URL or traceable artifact reference |
| `published_at` | Source-asserted publication or genuine artifact creation time, with timezone |
| `observed_at`, `available_at` | Genuine source observation/availability assertions, with timezone |

Copy genuine additional metadata into the entry as needed. It is retained
unchanged under `caller_metadata`; existing declared versions are never replaced
with generated versions. Do not invent values to fill these fields. Publication,
availability and identity assertions remain caller assertions. Future observation
or availability, wrong game, missing/mismatched hash, missing provenance and
postgame participation metadata produce explicit blockers. Missing or unreadable
files remain missing. Invalid inventory structure aborts rather than silently
dropping entries; partial output must not be used.

The complete original inventory and each readable artifact are copied as raw
bytes, hashed, inventoried and assigned a separate actual `frozen_at`. The
existing `pregame_validation.validate_evidence` checks are reused with this actual
freeze time as the captured version availability and the new archived path as
its reference. Original timestamp claims are never overwritten in caller
metadata. The existing canonical `evidence_fingerprint` seals manifest semantics;
it is **not** an invented engine Snapshot fingerprint.

All copied caller artifacts remain `CALLER_SUPPLIED_UNVERIFIED`. A copied file's
role is not proof of its contents. Even when all six roles are supplied, source
authenticity, exact quoted legs and bookmaker terms, forecast/model/input hash
bindings, real training provenance, player identities, starters and the complete
active universe require independent role-specific validation. This command
never emits a validator-ready ticket or an automatic clearance.

`missing_evidence` lists roles without an archived file. Separate artifact
`blockers` list problems with present files. Top-level `ticket_ready`,
`starter_verified`, `active_universe_verified`, `offered_quote_verified` and
`forecast_provenance_verified` stay false. Status is `RESEARCH_ONLY_BLOCKED`.
Existing historical forecasts and quotes are not certified by a new capture.

## Inventory, integrity and immutability

`manifest.json` includes the selected schedule/context references, capture times,
all retrieval attempts, supplied artifact metadata, missing roles, blockers, and
SHA256/byte counts for **every** evidence and metadata file. `manifest.sha256`
seals the raw manifest bytes; these two seal files are excluded from the manifest's
own inventory to avoid circular hashes. Capture uses exclusive creation and
flushes files to disk, then marks files read-only. It never modifies an existing
bundle. This is application-enforced immutability plus tamper detection, not
filesystem WORM storage; a privileged owner can change permissions and rewrite it.

Save the printed `manifest_sha256` separately and verify with that trusted value:

```powershell
python -m research.pregame_capture verify --bundle "C:\research-evidence\new-capture" --expected-manifest-sha256 <saved-digest>
```

Verification is offline and checks the closed file inventory (including extra or
deleted files), raw hashes, lengths, canonical fingerprint, manifest seal and
optional externally retained digest. Symbolic links/junctions and unsafe inventory
paths are rejected. A local seal alone cannot detect a fully rewritten and
resealed replacement; use the separate trusted digest. `PASS` means byte integrity
only, including integrity of archived failures. It never means complete evidence,
authentic sources, predictive skill or ticket readiness.

Capture exits 0 when it has archived a research bundle, including one with missing
evidence or HTTP failures. Verification exits 0 for integrity PASS and 1 for FAIL;
invalid CLI input or capture errors exit 2. Check manifest blockers separately.

```powershell
python -m pytest tests/test_pregame_capture.py -q
```

Tests cover the exact capture clock, raw byte/full inventory preservation, source
failures and identity mismatch, late and post-kickoff capture, missing evidence,
caller artifact preservation, external digest anchoring, tampering, and refusal
to overwrite. Fixtures establish software behavior only; they supply no measured
market coverage, settlements or model performance.
