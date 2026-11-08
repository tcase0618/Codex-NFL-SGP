# Optional game-scoped accuracy audit

`research.accuracy_swarm.run` accepts keyword-only `game_id` and `teams`
(two exact team abbreviations). The audit runner also accepts `--game-id` and
`--teams TEAM TEAM`. This is an evidence audit, not the engine operation CLI.
Omitting these options retains the complete 32-profile audit.

Game scope requires both teams in the persisted `upcoming_games`, with the exact
game ID, reciprocal opponents, and matching future kickoff. Each schedule source
must have an inspectable archive, matching SHA-256, and an observation no later
than the audit cutoff. The archived event is parsed using the refresh schedule
parser; its complete team context must equal the persisted records. Labels are
never guessed. Missing schedules, unknown/duplicate teams, altered event bytes,
duplicate events, empty profiles, and conflicting evidence scope are rejected.

Resolved profile paths may be under configured `runtime` or `output`. Existing
absolute archive paths are preserved. Scoped source checks approve the existing
runtime source/cache roots, runtime itself (reviewed crosswalks), and configured `output`, including repaired injury
archives there. Symlinks and traversal resolve before containment checks.
An approved location never bypasses archive hashes or profile content hashes.

Only selected profile sources and personnel are audited. Explicitly unrelated
known-team context gaps are excluded; selected-team, global, unknown-team, and
unattributed gaps still block. Personnel must match the sourced game/team/kickoff;
the existing personnel clearance requirements remain in force.

All seven auditors still execute. Canonical metric, all-11 lineup, charting,
calibration, backtest, ledger, and opportunity requirements are unchanged. Rebuilt
metric evidence continues to bind the complete base profile snapshot, database,
and code revisions. Existing model and exact forecast/source bindings are not
replaced by game scoping. A supplied game/profile scope conflicting with the
selected profiles is rejected; supplied model fields are preserved.

Reports retain `scope.game_id`, explicitly list `scope.profiles_selected`, and
include schedule archives and runtime state in the verified artifacts. A scoped
PASS is evidence validation only; `betting_ready` remains false. A BLOCKED report
does not authorize wagers. Invalid scope fails before a report is created.

The runner reads the existing state, config, profiles, database, and evidence;
it writes only its new report directory. Do not refresh, copy, backdate, or alter
runtime snapshots to obtain clearance.
