# Weekly games and game-specific research tiers

Matchups in the navigation now opens **Weekly matchups** on both team and player
screens. The current week comes from the NFL scoreboard's reported season/type/
week, not the host's ISO week or the selected historical profile. Every returned
game has one row sorted by kickoff, with opponents, team colors, kickoff/status,
and published bye teams listed separately. The week selector uses the provider's
calendar entries. Missing calendars show an unavailable state rather than a
partial guessed slate.

Selecting a row fetches `/api/week-matchup` with the exact calendar selection
and game ID. The service requires that ID to occur in the selected calendar;
the frontend additionally checks game/ticket IDs and comparison teams. It opens
both offense-versus-defense comparisons and four research cards: 3, 5 and 8
mixed-market legs, plus a distinct three-scorer anytime TD family.
No winning probability or score forecast is inferred from profile ranks.

The weekly service archives raw source bytes when `--schedule-archive` is set
and caches a calendar for five minutes. Current and explicit-week queries have
separate cache keys. Historical team metric filters cannot change the schedule.

## Ticket data contract

An optional `--ticket-ledger <checkpointed-ledger.sqlite>` links actual recorded
research. The ledger is opened read-only, the hash chain and event semantics
are replayed, and its byte hash is rechecked after selection. Displayed tickets
must match the selected game ID, kickoff and both participating teams. Only
3-, 5-, or 8-leg proposals appear in the corresponding tier. A three-leg
proposal whose every leg is `anytime_td` at line 1 appears only in the TD card;
other three-leg proposals remain in Tier 1. Provided combined
quotes must identify FanDuel; unpriced research can be displayed with price
unavailable. All quotes remain frozen observations, not live offers, and no
Tennessee quote verification is inferred from the provider name.

Each stored proposal retains its research/blocked status and recorded settlement
status. This is an observation display, not a way to grant betting authority.
The UI never fabricates offered lines, player identities, projections or tickets.
When genuine frozen tickets are absent, all four cards say **Not generated yet**.
An invalid ledger shows unavailable, with no copied or inferred tickets.

## Validation

106 Python schedule, ledger and team-quant checks passed. Three Node checks cover
tier rendering, escaping/frozen-price labeling, and exact game/team identity.
JavaScript syntax checks pass. Live Week 9 retrieval returned 15 games and the
published TEN/PIT bye entries. Two real game-detail requests matched calendar
IDs/opponents, returned all three missing-ticket states, and an unrelated game
ID returned HTTP 404. Evidence is under the task's `outputs/weekly-matchups-ui`.
No source profiles, refresh automation, wager authority, or purchases changed.

Visual design self-assessment: 8/10 for consistent dark surfaces, row hierarchy
and team accents. Screenshot and narrow-panel rendered review remain unavailable
because local browser control is disabled; no visual verification is claimed.
