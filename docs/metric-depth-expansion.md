# NFL metric depth expansion — November 5, 2026

The local player/team terminal now reads a new player snapshot and independent charting and opponent-context overlays. Existing base snapshots and historical reports remain intact. This is sourced descriptive research; coverage is not predictive accuracy or betting readiness.

## Verified coverage

| Population | Before | After |
|---|---:|---:|
| 32 teams × 401 season-view slots, 2026 | 5,606 / 12,832 (43.69%) | 6,214 / 12,832 (48.43%) |
| Qualified team research samples | 4,152 | 4,597 |
| 510 observed player-seasons, 2026 | 2,282 / 22,220 (10.27%) | 5,647 / 22,220 (25.41%) |
| Caleb Williams, 2026 | 5 / 65 | 18 / 65 |
| Chicago, 2026 | 176 / 401 | 195 / 401 |

The player denominator covers observed player-seasons, not all current roster profiles. The current-roster baseline used 2,595 profiles and 105,620 slots; no new all-roster percentage is asserted. Different sample populations must not be compared as if identical. Populated values include small or incomplete samples. Supplemental snap counts and NGS rows are separate and do not inflate catalog coverage.

## Implemented behavior

- `research.enrich_player_metrics` produces a new snapshot using stable GSIS attributed play events. Thirty-seven adapters add EPA, success, explosive, touchdown and situational measurements with numerator/denominator evidence and collection-time cutoffs. Available source observations extend from 2006 through 2026; this does not imply equal field or positional coverage.
- `research.public_charting` archives and hash-checks public FTN, historical participation, snap-count, identity-crosswalk and NGS inputs. The independent charting reader supplies 45 supported team IDs where exact source fields exist. For 2026, supported populated IDs increase from 2 to 16 per team: 448 additions.
- `research.build_team_context_metrics` supplies five existing IDs across all 32 teams: 160 additions. Opponent adjustment is a retrospective descriptive calculation using independent league/opponent pools excluding focal-team games, not a trained future rating.
- Current sourced roster/depth counts appear separately from the 20 advanced personnel ratings. No listed name is treated as proof of game-day activity, healthy status, starter availability or historical continuity.
- Player pages show exact-season identity-mapped offense/defense/special-team snap counts and public NGS measurements. NGS week-zero provider aggregates remain separate from weekly measurements and team stints. These cards do not substitute for routes, pass-block snaps, coverage snaps or proprietary grades.
- Matchups now contain eleven explicitly matched comparisons per offense direction. Explosive passing uses completed passes gaining 20+ yards divided by competitive dropbacks on both sides. Overall red-zone, passing red-zone and rushing red-zone each have matching defensive cohorts. Comparison values/ranks remain descriptive; projected scores, winner probabilities and betting edges are withheld without a validated model.
- Evidence inspection follows the adapter supplying the value. Charting/context expose labeled aggregate evidence and exact original source IDs; the endpoint does not invent sampled play rows. Base metrics retain actual play observations.
- The read-only MCP server exposes eleven tools covering player/team profiles, metrics, coverage, matchups and evidence. It forwards the loopback API and preserves source and transport hashes. No global MCP installation/configuration is claimed.
- Player pages default to the latest observed season. Observed metrics appear first within categories; categories without observations remain inspectable. Existing black background, team colors and standalone weekly Matchups navigation remain.

## Audit corrections

The independent auditor checked matching cohorts, source identity, missingness, period boundaries and arithmetic. Corrections include preserving charting IDs without a second prefix, respecting explicit incomparable flags, selecting the correct evidence adapter, using consistent WR touchdown/target percentage scale, and handling empty import-summary metadata. A fixture with one touchdown in ten targets yields 10% under both weekly and play-derived adapters.

Independent checks include 192 matched-cohort calculations, six charting numerator/denominator replays, four opponent-adjustment replays, four player EPA replays, source hash verification and qualified-peer percentile checks. Zero unresolved source IDs occurred in the 2026 integrated census. Both old base snapshots retain their original hashes.

## Verification and runtime

Final parent full Python suite: **1,184 passed, 1 skipped**. Team visual tests: **8 passed**. JavaScript syntax checks passed. Local API checks verified the v5 player profile, source-resolved Chicago profile, charting/context aggregate evidence and the eleven matched factors. Browser-rendered visual QA was not performed in this run.

Main UI: http://127.0.0.1:8766/ ; teams: /teams ; weekly games: /matchups.

Runtime evidence and independent review are under:
`C:\Users\tcase\Documents\Codex\2026-11-02\referenced-chatgpt-conversation-this-is-an\outputs\metric-depth-expansion`

Use `live-launch.json`, `live-api-verification.json`, `player-manifest-verification.json`, `auditor/final-audit-review.md`, and the role-specific manifests for exact inputs and checks. The first cold Chicago profile required 29.24 seconds; repeated requests reuse calculation caches. MCP smoke after the live reload passed all 11 tools, with matching direct-API payloads and response hashes; evidence is in `mcp/parent-final-smoke-20261105-v1`.

## Remaining source limits

6,618 / 12,832 team catalog slots remain unavailable. The advanced personnel ratings remain 0 / 640 league-wide. Current-season all-11 participation and coverage shells, proprietary assignments/grades and validated personnel ratings are not fabricated. Public 2024/2025 participation records are never relabeled as 2026. Public sources and source-vintage history are partial. Provider EPA/tracking training vintage, predictive calibration, historical pregame eligibility and historical offered prices remain unverified. No new bets, forecast probabilities, execution authority or historical win-loss claims were created.
