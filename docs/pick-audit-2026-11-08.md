# Pick audit — TB @ CHI, Nov 8 2026 (final CHI 27, TB 24)

Source: ESPN final box (event 401873050), pulled 2026-11-09 12:31 UTC. Prices offered by the book were not recorded,
so P&L and closing-line value cannot be computed. Probabilities are the ones quoted when each pick was made.

## Tickets

| # | Ticket | Quoted P | Result | Killed by |
|---|---|---|---|---|
| 1 | Pregame 3-leg core: Irving o29.5 rush, Burden o2.5 rec, Loveland o2.5 rec | 54% | **WIN** | — |
| 2 | Pregame 5-leg: core + Odunze o29.5 rec yds + Mayfield o0.5 pass TD | 25% | LOSS | Mayfield 0 pass TD |
| 3 | Swift anytime TD (single) | 45% | LOSS | Swift 0 TD |
| 4 | Anytime TD x3: Swift, Irving, Burden | 6% | LOSS | Swift (Irving 2 TD, Burden 1 TD hit) |
| 5 | "Chicago wins" 3-leg: CHI ML, Burden o29.5 rec yds, Loveland o29.5 rec yds | 34% | **WIN** | — |
| 6 | Upside 4-leg: Irving o49.5 rush, Burden o49.5, Odunze o49.5 rec yds, Mayfield o0.5 pass TD | 10% | LOSS | Mayfield 0 pass TD (3 of 4 hit) |
| 7 | Live 5-leg: CHI ML, Mayfield o199.5, Irving o39.5, Swift o39.5 rush, total o35.5 | 57% | LOSS | Swift 31 rush yds (4 of 5 hit) |
| 8 | Live 8-leg: + Williams o249.5, Burden o3.5 rec, Loveland o2.5 rec | 37% | LOSS | Swift 31 rush yds (7 of 8 hit) |
| 9 | Lottery: Chicago blowout (CHI -13.5 …) | 6.4% | LOSS | CHI won by 3 |
| 10 | Lottery: Tampa comeback (TB +3.5, TB TT o16.5, Mayfield o249.5, Irving ATD) | 6.1% | LOSS | Mayfield 243 yds (missed by 7; other 3 hit) |
| 11 | Lottery: Shootout (total o53.5 …) | 1.9% | LOSS | Total 51 |

2 wins in 11 tickets vs 2.8 expected from the quoted probabilities.

## Legs vs quoted probability

| Group | Legs | Hit | Expected |
|---|---|---|---|
| Pregame legs | 14 | 12 | 8.8 |
| Live halftime legs | 8 | 7 | 7.0 |
| Lottery legs | 11 | 3 | 4.2 |
| **All** | **33** | **22** | **20.0** |

Pregame legs hit well above their quoted probabilities, consistent with the backtest finding that raw P(over)
was too low (it blended in the chance of not playing). Live legs matched expectation exactly.

## Failure analysis

1. **Mayfield passing TD (o0.5) sank two tickets.** Tampa's touchdowns came from Irving rushes; Mayfield threw 0 TD, 2 INT.
   The leg was 64–71% and was in a validated market — a legitimate miss, but it was the lowest-probability leg on both tickets.
2. **Swift o39.5 rushing sank both live tickets.** The live simulator gave Chicago's carries to Swift because Monangai was
   inactive, but Salvon Ahmed (5 car, 25 yds) was not in the projection pool. Same failure family as the McCarthy bug:
   backups absent from the pool inflate the starter's share. The simulator also ignored first-half evidence
   (Swift 24 yds on 6 carries).
3. **The comeback lottery script happened** (TB covered +3.5, scored 24, Irving 2 TD) and missed on one yardage leg by 7 yards.
   The correlation design was right; the leg's threshold was too high.
4. Wins 1 and 5 were built on receptions/receiving-yards legs. The backtest found ~0% skill for receptions,
   so these wins are not evidence of edge.
5. Game-level call (no side/total bet): market CHI -6 failed to cover (CHI +3); total 51 vs 47.5 went over;
   the model's 46.7 lean was wrong but was correctly sized as "no bet".

## Fixes queued
- Always include every active RB/WR on the depth chart in the projection pool (not only recent stat-producers).
- Live mode: update player shares with first-half usage before simulating the second half.
- Record offered and closing prices for every pick in the ledger; without them results cannot be judged.
