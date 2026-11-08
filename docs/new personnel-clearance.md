# Personnel clearance evidence contract

`audit_personnel(profile, as_of)` accepts one optional
`profile.personnel.clearance_certificate` object. Automatic profiles without it
retain their original blockers. This document is a producer contract, not a real
certificate or a claim that current football inputs are ready.

The certificate is a reviewed, normalized evidence archive. The producer must
retain authentic publisher material and verify its extraction and completeness
before issuing a certificate. The audit rehashes the embedded UTF-8 JSON, validates
its content against the snapshot, and replays transactions. It does not fetch a
publisher, authenticate a reviewer, verify signatures, inspect external files, or
establish that a publisher's claims are true. Arbitrary self-authored JSON and a
matching hash are not authentic evidence. `archive_content_reverified` means the
embedded certificate and identity archives passed content checks only.

## Snapshot and review binding

The existing profile gains three strings when using this optional contract:
`game_id`, `kickoff` (aware ISO timestamp), and `snapshot_id` (immutable producer
snapshot ID). Existing `team_id` and `as_of` must also be nonempty strings.
The certificate fields are:

| Field | Requirement |
| --- | --- |
| `schema` | Exactly `PERSONNEL_CLEARANCE_V1` |
| `scope` | Object containing exactly `team_id`, `game_id`, `kickoff`, `snapshot_id`, `as_of`, equal to the profile values |
| `snapshot_sha256` | SHA256 of the entire profile excluding `personnel.clearance_certificate`, using Python-compatible `json.dumps(sort_keys=True, separators=(',', ':'), allow_nan=False)` encoded as UTF-8 (default ASCII escaping) |
| `reviewed_at` | Aware timestamp equal to profile `as_of` |
| `reviewer_reference` | Nonempty accountable review reference |
| `archives` | Object with exactly `roster`, `depth`, `injury`, `active`, `transactions` |

The snapshot must precede or equal the audit cutoff, and the cutoff must be
strictly before kickoff. Source observations must be no later than the snapshot
and at most 24 hours old at the audit cutoff. Existing depth chart freshness,
identity review, duplicate ID, review-flag, and roster reconciliation rules still
apply. A certificate never overrides those blockers.

## Archive envelopes and completeness

Each archive envelope has `reference`, `content_sha256`, and `archive_content`.
`archive_content` is exact archived JSON text; its UTF-8 bytes must hash to the
lowercase SHA256 value. Duplicate JSON keys, nonfinite constants, and non-object
payloads are rejected. Each payload contains:

| Field | Requirement |
| --- | --- |
| `kind` | Its archive key |
| `scope` | Exact certificate scope |
| `observed_at` | Aware source observation timestamp |
| `complete` | Boolean `true`, attesting full source coverage |
| `pagination_complete` | Boolean `true`, attesting all pages were captured |
| `row_count` | Integer exactly equal to `len(rows)`; booleans rejected |
| `rows` | Explicit list; missing rows never mean a complete empty report |

Roster/depth/injury envelopes must exactly identify the existing source record's
reference, hash, and retrieval timestamp. This contract expects those records to
describe the normalized archived evidence, not a different raw response or image.
Retain original publisher archives separately for accountable source review.
Do not replace a raw-source hash with a normalized hash and pretend they are the
same artifact.

Every roster, depth, active, injury, and transaction member row has
`player_reference` and `provider_identities`. Provider identities are a nonempty
object containing only nonempty string `gsis_id` and/or `espn_id` values. Profile
players require exact ID equality. Names and URLs cannot substitute for IDs.
Duplicate references or provider-ID collisions in a row collection are rejected.

Roster, depth, and active rows must each exactly cover the supplied profile
players, with no omitted or additional identity. Thus a selected-player fragment
cannot certify a full roster. Roster rows additionally have `roster_sections`
equal to the player's existing value, and the roster payload must declare
`covered_sections` exactly `['Active', 'Reserve', 'Practice Squad', 'Other']`.
This is an explicit completeness attestation even for sections with zero rows.
Existing non-Active membership blockers remain in force; this change does not
authorize reserve or practice-squad players.

Depth rows carry exact profile `depth_roles`. Injury rows equal the entire
`personnel.injury_report_rows` list, including IDs and existing nonempty `columns`.
Injury rows may be a subset of known players. A complete zero-injury report can
use `rows: []`, `row_count: 0`, and the same empty profile list, with all other
scope, source, review, completeness and archive checks satisfied. Missing or
unavailable reports remain blocked.

The active payload must declare
`evidence_type: 'OFFICIAL_GAME_DAY_ACTIVE_LIST'`. Every activity row must
affirmatively say `status: 'ACTIVE'` for this game. Roster membership, depth
placement, a blank injury report, or absence from inactives is insufficient.

## Transactions and identity archives

The transactions payload additionally contains `baseline_rows`, `window_start`,
and `window_end`. Baseline rows have the exact roster-row shape and represent
membership at `window_start`. The complete interval must end at snapshot `as_of`;
`observed_at` must equal that end, so an earlier retrieval cannot certify later
coverage. A zero-event interval still needs a complete baseline and explicit
empty `rows`.

Each transaction row contains a unique nonempty `transaction_id`, aware
`effective_at`, `action` (`ADD` or `REMOVE`), and `member` (a full roster row).
Events must be ordered within the baseline/snapshot interval. ADD must introduce
an absent reference; REMOVE must exactly match an existing member. A membership
change requires REMOVE then ADD. Replaying the complete interval must yield the
exact archived roster, including IDs and sections. Unsupported actions, omitted
events that prevent reconciliation, and interval gaps block. Authentic source
review must establish that the declared interval truly contains every event;
the audit cannot discover undisclosed publisher events from supplied JSON.

For certified clearance, each existing `identity_review.sources` entry also
needs `archive_content`; its existing `sha256` must hash those exact UTF-8 bytes.
The decoded object must equal the review's `team_id`, `player_reference`,
`position`, `provider_identities`, `matched_biographical_fields`, plus
`kind: 'identity'` and `observed_at` equal to the source timestamp. Existing
requirements for two distinct source references, at least three distinct
biographical fields, review chronology, and exact profile identity still apply.
This hashes and binds the reviewed mapping; it does not independently authenticate
the biographical comparison or publisher.

## Result and remaining real inputs

`evidence.certificate_issues` returns exact field paths and requirements. Invalid
certificates retain all original personnel completeness, game-active,
transaction, and injury-game-scope blockers. All four gates clear together only
after this contract validates; all other original checks still determine PASS.
No probability thresholds, forecast criteria, betting permission, or execution
controls change. Synthetic tests prove validator behavior only.

Real clearance still requires authentic full roster/depth/current injury
coverage, affirmative official target-game activity for every supplied player,
a source-complete transaction baseline and interval, stable provider mappings
with archived independent identity reviews, an accountable review, and exact
game/snapshot/time/hash bindings. No such real certificate is supplied here.
