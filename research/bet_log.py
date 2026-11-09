"""Bet log: record offered price, closing price and result for every ticket (Nov 9 audit fix).

    python -m research.bet_log add  --ticket "BUF@MIN safe 3-leg" --legs "a|b|c" --model-p 0.676 --price -150 --stake 10
    python -m research.bet_log close --id 3 --closing -170
    python -m research.bet_log grade --id 3 --result win|loss|push|void
    python -m research.bet_log report
Profit/loss, ROI and closing-line value (CLV = implied(closing) − implied(offered)) are computed
only from recorded prices — never estimated.
"""
import argparse
import sqlite3

from nflquant.config import settings

DDL = """CREATE TABLE IF NOT EXISTS bets (id INTEGER PRIMARY KEY, placed_at TEXT DEFAULT CURRENT_TIMESTAMP, ticket TEXT,
legs TEXT, model_p REAL, price INTEGER, stake REAL, closing INTEGER, result TEXT, payout REAL)"""


def implied(a):
    return -a / (-a + 100) if a < 0 else 100 / (a + 100)


def dec(a):
    return 1 + (a / 100 if a > 0 else 100 / -a)


def db():
    con = sqlite3.connect(settings().data_dir / "bets.sqlite")
    con.execute(DDL)
    return con


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["add", "close", "grade", "report"])
    ap.add_argument("--id", type=int)
    ap.add_argument("--ticket")
    ap.add_argument("--legs", default="")
    ap.add_argument("--model-p", type=float)
    ap.add_argument("--price", type=int)
    ap.add_argument("--stake", type=float)
    ap.add_argument("--closing", type=int)
    ap.add_argument("--result", choices=["win", "loss", "push", "void"])
    a = ap.parse_args()
    con = db()
    if a.cmd == "add":
        cur = con.execute("INSERT INTO bets (ticket, legs, model_p, price, stake) VALUES (?,?,?,?,?)",
                          (a.ticket, a.legs, a.model_p, a.price, a.stake))
        print("logged bet", cur.lastrowid, f"edge vs offered: {a.model_p - implied(a.price):+.3f}" if a.model_p else "")
    elif a.cmd == "close":
        con.execute("UPDATE bets SET closing=? WHERE id=?", (a.closing, a.id))
    elif a.cmd == "grade":
        stake, price = con.execute("SELECT stake, price FROM bets WHERE id=?", (a.id,)).fetchone()
        payout = {"win": stake * dec(price), "loss": 0.0, "push": stake, "void": stake}[a.result]
        con.execute("UPDATE bets SET result=?, payout=? WHERE id=?", (a.result, payout, a.id))
    else:
        rows = con.execute("SELECT id, ticket, model_p, price, stake, closing, result, payout FROM bets").fetchall()
        staked = sum(r[4] or 0 for r in rows if r[6]); returned = sum(r[7] or 0 for r in rows if r[6])
        clv = [implied(r[5]) - implied(r[3]) for r in rows if r[5] is not None]
        for r in rows:
            print(r)
        print(f"graded stake {staked:.2f}  returned {returned:.2f}  P/L {returned - staked:+.2f}  "
              f"ROI {((returned - staked) / staked if staked else 0):+.1%}  mean CLV {sum(clv) / len(clv) if clv else float('nan'):+.3f} ({len(clv)} closed)")
    con.commit()


if __name__ == "__main__":
    main()
