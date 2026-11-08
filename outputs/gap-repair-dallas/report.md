# Dallas transaction recovery

Result: PARTIAL; season completeness remains unverified. No runtime integration performed.

The official annual Dallas route and bare /team/transactions/ route return 404.
The working official club news tracker supplies 13 literal October statements,
dated October 1-29, with club identity and publication date checked. It calls
these latest transactions and omits November. Multi-player statements remain
statements, with unresolved player identities; they are not split or matched by name.

The six NFL categories yielded six Dallas October rows (one trade, four waiver
rows, one termination) and no Dallas November rows. Counts are source rows, not
unique reconciled transactions; the club statements overlap the league rows.
October signings, reserve-list, waivers and terminations repeat archived page
content. October Other fails player/team identity verification. November Trades
has no recognized transaction report, so the month remains partial even though
the other five observed nonempty league tables expose no next page.

The initial October and November signings probes repeated their first pages
byte for byte at the exact published cursor URLs. Collection responses differed
from those initial probes: October advanced once then repeated, and November
returned only one signing row without a next link. Source variability remains
unexplained. No claim is made that a stable cursor repair was found. The collector
follows only the publisher's actual after link and never invents cursor values,
page numbers, cache-busting parameters, or an API route. Inspection of the NFL
page markup and its linked main.js loader disclosed no alternate transaction API.
This was bounded exploration, not an exhaustive search of every official asset.

Keep contradictory source dates and names visible: for example, club statements
place the Rogers/Demercado waivers on October 27, while NFL waiver rows include
October 27 and 28. No deduplication across sources or reconciliation is asserted.

Evidence: recovery.json contains selected year/month/category and exact Dallas
team links, rows, source references, retrieval times, absolute archives and SHA256.
exploration/ preserves nine probes (including 404 bodies and the loader);
verified-run/archive/ preserves nineteen collection responses and provenance.
validation.json verifies all 28 archived probe/collection provenance records and
records implementation/test hashes. The final JSON was reparsed offline from the
archived responses using the final implementation; no further network calls.
Retrieval was November 4 UTC / November 3 America/New_York.

Limits: two selected calendar months, three pages per category, maximum 38
collection requests, actual 19 plus nine exploration requests; no retries or
redirects; 5-second connect / 20-second read timeout and 4 MB body limit per
response. Pages with malformed scope, identity, schema or provenance fail closed.
Empty windows never certify zero transactions or completeness. Even observed
nonempty pagination exhaustion is not independent proof of full source history.

Validation: 40 new collector tests plus 56 source_gap_repair regression tests;
96 passed, zero failures/errors/skips. See tests.xml and validation.json.

Only the new collector, its new tests and this output directory were written.
source_gap_repair.py was not edited. No force refresh, config, runtime state,
other repo, subscription or bet changes were made.

## Suggested parent integration (not applied)

Import collect_window and collect_tracker from research.dallas_transaction_recovery.
Use collect_window(season, month, archive, max_pages=3) for an isolated evidence
attachment and optionally collect_tracker(season, month, archive). Fetching these
must remain an explicitly selected research action, outside automatic refresh.
Attach their complete result objects under a Dallas recovery evidence field;
keep status PARTIAL_OFFICIAL_WINDOW and all_six_categories_complete false for
this evidence, and preserve the existing Dallas source gap. Do not replace the
annual history, merge club statements into normalized NFL player rows, infer
current roster membership, or authorize eligibility/execution. The callback
contract requires status_code, reference, resolved_reference, retrieved_at,
archive_file and sha256; the parent's existing collect_evidence callback does
not provide that contract and must not be passed directly.

Reproduce bounded collection with a NEW output directory:
python -m research.dallas_transaction_recovery --year 2026 --months 10 11 --max-pages 3 --output outputs/gap-repair-dallas/new-run
