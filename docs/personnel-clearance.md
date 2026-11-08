# Personnel certificate diagnostics and remaining clearance gap

The audit currently supports **inspection only**, not authenticated clearance.
`PERSONNEL_CLEARANCE_V1` is a candidate evidence schema. Even a structurally
consistent candidate remains BLOCKED. `clearance_supported` and
`source_authority_verified` are false. No real certificate was created or tested.

This document supersedes the interrupted draft `new personnel-clearance.md` where
that draft describes matching normalized archives as sufficient to clear gates.
The draft is preserved as existing work; its clearance claim is not supported by
the final implementation.

## Why matching hashes cannot grant clearance

The caller supplies both the archive bytes and their expected hashes. It also
supplies inventory rows, source references, retrieval times, pagination flags,
completeness flags, and reviewer labels. A producer can invent a one-player
inventory, hash that inventory consistently, and label its source official.
Neither internal equality nor an official-looking URL proves that the source was
acquired from the publisher, contains every publisher row, or is a game-day active
list. Identity archives can likewise repeat invented IDs and biography-field
names without proving the underlying biographical comparison.

The permitted input contract has no independent publisher-authentication anchor
or trusted extraction/coverage verifier. Adding another caller-controlled
`verified`, `signature`, `publisher`, or review field cannot create one. The final
audit therefore never removes the original completeness, game-active,
transaction-reconciliation, or injury-game-scope blockers. Original profiles
without a certificate keep the original reason codes.

## Candidate fields inspected

Using the optional `personnel.clearance_certificate` adds profile string fields
`game_id`, `kickoff` (aware timestamp), and `snapshot_id`. The certificate contains
`schema: PERSONNEL_CLEARANCE_V1`, `scope`, `reviewed_at`, `reviewer_reference`,
`snapshot_sha256`, and `archives`.

`scope` must exactly equal the profile's `team_id`, `game_id`, `kickoff`,
`snapshot_id`, and `as_of`. Review time equals snapshot time; the snapshot must be
no later than the audit cutoff, and the cutoff strictly before kickoff.
`snapshot_sha256` hashes the entire profile excluding the certificate using
UTF-8 `json.dumps(sort_keys=True, separators=(',', ':'), allow_nan=False)` with
default ASCII escaping.

`archives` has exactly `roster`, `depth`, `injury`, `active`, and `transactions`.
Each envelope supplies a distinct nonempty `reference`, `content_sha256`, and
exact UTF-8 JSON text in `archive_content`. The hash must match those bytes.
Duplicate JSON keys, nonfinite constants and non-object payloads are rejected.
Every payload has exact `kind` and `scope`, aware `observed_at`, explicit boolean
`complete` and `pagination_complete`, explicit `rows`, and integer `row_count`
equal to the row count. Observations must precede or equal snapshot time and be
within 24 hours of the audit cutoff. Roster/depth/injury reference, hash and time
must match the corresponding existing source record. These are consistency
checks on normalized candidate archives, not proof about an original HTML page,
image, external file or publisher response. Do not substitute a normalized hash
for a different raw artifact's hash.

Rows must have exact `player_reference` and nonempty string `gsis_id` and/or
`espn_id` in `provider_identities`. Name-only joins, duplicate references, ID
collisions and mismatched profile IDs fail inspection. Roster, depth and active
rows must each exactly cover the profile's supplied players. Roster sections and
depth roles must match those supplied players. Roster `covered_sections` must
explicitly list `Active`, `Reserve`, `Practice Squad`, `Other` in that order.
This declaration still cannot prove that omitted real publisher rows do not exist.

Injury rows must equal the profile injury rows, including stable IDs and nonempty
`columns`. An explicit empty list can be structurally consistent; it never clears
the original missing-injury-coverage blocker without authenticated coverage.
Active payload `evidence_type` must be `OFFICIAL_GAME_DAY_ACTIVE_LIST` and each
row's `status` must be `ACTIVE`. These labels are checked but not trusted. Roster
membership, depth placement and absence from inactives never imply game activity.

Transactions include `baseline_rows`, `window_start`, and `window_end`. The end
and transaction observation time must equal snapshot time. Ordered `ADD`/`REMOVE`
events, with unique `transaction_id`, aware `effective_at`, and exact `member`
rows, must replay the baseline to the exact supplied roster. This detects internal
gaps; it cannot detect undisclosed publisher events when the caller fabricates
both the baseline and final roster.

Existing identity review sources additionally need `archive_content` to pass
candidate inspection. Their `sha256` hashes its bytes. The decoded object must
equal the review's team, player reference, position, provider identities,
`matched_biographical_fields`, plus `kind: identity` and the source's
`observed_at`. All original source/time/review/membership/depth/ID blockers remain.

## Inspectable results and authentic inputs still needed

`certificate_structure_verified` reports internal consistency only.
`archive_content_reverified` reports successful embedded content/structure checks,
never publisher authority. `certificate_issues` identifies exact missing or
mismatched field paths and separate `SOURCE_AUTHORITY_UNVERIFIED` issues for all
five archive kinds and identity-review sources. `publisher_provenance` paths are
diagnostic descriptions of missing independent verification, not an accepted
caller field or a magic trust flag.

To implement genuine clearance, a separately authorized integration must supply
independently verifiable publisher acquisition and complete raw-source extraction
for the exact full roster, depth chart, current game-scoped injury report,
affirmative official target-game active proof, transaction baseline and every
event through the snapshot, and independently verified stable-ID mappings with
actual biographical comparison evidence. It must bind those results to exact
game/team/snapshot/times and archive bytes, with a trusted verification mechanism
outside the candidate's own assertions. No such mechanism is added here.

Automation remains **8am America/New_York on game-day mornings only**. Missing or
not-yet-available evidence at that run must remain BLOCKED. This work adds no
refresh, later run, source acquisition, runtime settings, betting authority, or
probability/eligibility relaxation. Parent integration owns any future source
verification work.
