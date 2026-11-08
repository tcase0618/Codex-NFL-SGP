# Four-agent repair and current review

The repair workflow separates an independent auditor, two fixers (personnel and
forecast evidence), and a coordinator who dispatches finding IDs and acceptance
criteria. A dispatch or a finished edit does not count as independently verified
closure. Interrupted reviews remain interrupted until an actual review runs.

## One current review command

Run `python -m research.readiness_review --config <config.json>
--team-database <teams.sqlite> --game-id <event-id> --teams <TEAM> <TEAM>
--output <new-directory> --evidence <evidence-manifest.json>`.

Optional `--capture <immutable-capture-directory>` assembles genuine supplied
artifacts using `research.readiness_evidence`. Optional `--inventory <inventory.json>`
adds existing role artifacts. `--expected-capture-sha256` checks an externally
recorded capture digest. Missing roles remain missing. `--personnel-sources
<source-envelopes.json>` builds a projected exact-game personnel view by replaying
raw sourced archives; this view does not replace canonical profiles or certify
activity. No network calls or source-state writes occur in the review command.

Every run uses the actual current clock, creates a new directory, runs all seven
auditors, and verifies input hashes before and after the review. The command
returns exit code 2 while betting readiness is unestablished, including when
internal evidence is consistent but predictive performance is unapproved.
`review.json` contains exact input hashes, all remaining auditor reasons,
preparation results, and a hash of the canonical audit report.

## Scope bindings

In a selected-game audit, successful opportunity checks must match the sourced
game, kickoff instant, current profile versions, and selected model digest.
Successful calibration must cover that same model. Historical validation is not
required to contain the upcoming game's settlement. Instead, the selected
calibration model artifact must explicitly bind the historical cohorts:

```
validation_evidence:
  markets: [<supported market names>]
  backtest_sha256: <canonical JSON hash of complete backtest manifest>
  ledger_sha256: <byte hash of historical ledger archive>
```

These references are part of the hash-bound model artifact. Current opportunity
markets must occur in the validation coverage and actual backtest tickets.
This is an internal applicability check, not independent source authentication
or statistical approval. Default all-team audits preserve their prior behavior.

## Recording versus settled validation

`football_engine.ledger.audit_current_recording_evidence` verifies selected,
future, frozen pregame proposals and returns `RECORDED_UNSETTLED` when their
storage, chronology, identities and semantics are valid. Unpriced or blocked
research proposals may be recorded; the result exposes quoted proposal counts.
It never asserts eligibility, forecasts, prices or betting readiness.

`audit_ledger_evidence` remains the separate completed historical-cohort check.
Its settlement, full-ticket result, archive and immutability requirements are
unchanged. The current-recording result cannot replace it in the seven-auditor
gate. Missing actual model, forecast, active proof, executable prices, and
untouched evaluation evidence cannot be supplied by an agent vote or test data.

An evidence manifest may separately provide `current_recording.ledger` (absolute
path and byte hash) and `current_recording.ticket_ids`. The integrated review
audits these current records independently. With no current record, it reports
`UNAVAILABLE`; it creates no dummy proposal. The top-level `ledger` manifest
continues to identify the completed historical validation cohort.
