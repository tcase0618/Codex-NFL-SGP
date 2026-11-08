# Exact-game projected personnel recovery

`research.game_personnel_recovery.build_game_personnel_view(state, profiles,
sources, *, game_id, as_of)` returns a deterministic, read-only projection.
`state` is the existing parsed refresh state with `upcoming_games`; `profiles`
is the existing artifact wrapper or team map. Both participants must agree on
the event, opponent, kickoff, season, phase and week. The archived ESPN schedule
must actually contain that exact pregame event. No configuration/state is written.
Each selected profile's `version` must equal the existing canonical
`content_hash({k: v for k, v in profile.items() if k != 'version'})`.
Missing, modified or mismatched versions are rejected before quoting a profile
version in the derived projection. This verifies content consistency, not
publisher authentication or active clearance.

`sources` is a list of envelopes with `kind`, `team_id`, `reference`,
`resolved_reference`, `http_status`, `observed_at`, `archive_file`, and `sha256`.
Kinds are club `roster`, `depth`, `injury`, optional club `identity_profile`,
or `espn_roster`. Replay verifies raw-byte SHA-256 and observation cutoff.
Official source ownership and supported endpoint paths are checked. Existing
schedule envelopes lack explicit HTTP status; their preserved archived content
is replayed, without claiming newly authenticated acquisition.

Club roster and depth rows are re-extracted with the existing table parser and
joined only by exact club-player URLs. Injury reports use the existing scoped
parser, preserving unknown cells and incomplete reports. Source timestamps are
observation times, never historical availability or final-active proof.

ESPN roster identities require agreement between the explicit athlete ID, UID
and linked ESPN playercard. These are provider identities bound through the
provider's team roster to the exact schedule event; they are not automatic
matches to similarly named club players. Club-to-ESPN mappings require the
actual exact club-player profile to link to the explicit ESPN playercard.
Existing asserted IDs are cleared in the derived club view unless replayed
cross-link evidence supports them. Inputs are never altered. Missing cross-links
remain unresolved even when names match. GSIS IDs are not inferred.

Result `teams[team].club_players` preserves projected source context and
player-specific missing-ID, roster, depth, injury, active and transaction gaps.
`provider_players` contains independently sourced stable ESPN identities with
raw-source pointers. `missingness` reports exact recovered counts separately.
Market dependencies distinguish selected-player yardage/TD inputs from all-11
interaction models; no market requires every reserve to be active.

This output is not a clearance certificate and does not change existing gates.
`game_day_active_proof`, `trusted_acquisition_supported`, `clearance_supported`,
`historical_pregame_version_verified`, and `betting_ready` are always false.
ESPN roster injury/status labels remain provider context, not official game
reports. A successfully hashed archive proves replay integrity, not publisher
authentication. No roster membership, active roster section, lack of injury row,
or absence from inactives establishes game-day active status.

CLI: `python -m research.game_personnel_recovery --state STATE --profiles PROFILES
--sources MANIFEST --game-id 401873037 --as-of AWARE_TIMESTAMP --output NEW_JSON`.
The CLI refuses to overwrite output files. The integrated readiness entrypoint
is parent-owned; this module is an opt-in input producer, not runtime promotion.

Independent auditor review resumed and completed after the earlier service
limit. New versioned synchronization reports supersede the earlier incomplete
review status while preserving the prior reports as historical artifacts.
