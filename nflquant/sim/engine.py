"""Vectorized possession/play-level Monte Carlo NFL game simulator.

All N simulations advance one snap per step with masked NumPy updates. State per
simulation: quarter, clock, possession, yardline_100, down, distance, score,
overtime possession bookkeeping. Randomness comes from one seeded
``numpy.random.Generator`` so a (spec, n, seed) triple is reproducible.

Modelled: opening/half/OT kickoffs with empirical starting field position;
situational dropback probability (fitted logistic + team tendency offset); sacks,
scrambles, targets, completions, interceptions (with empirical air/return),
fumbles lost, offensive/defensive penalties, first downs, touchdowns, XP / two-point
tries, field goals (distance logistic), punts (empirical net by field position),
4th-down go/FG/punt (fitted multinomial), turnover on downs, safeties, clock
runoff (empirical by outcome and hurry-up state), end-of-game kneels, quarter
breaks, halftime possession, and overtime under the season's rules.

Not modelled explicitly: timeouts (their effect is inside the empirical hurry-up
clock distributions), spikes, onside kicks, two-point decision optimisation,
in-game injuries. These are documented limitations.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .league import League, fourth_design, pass_design

PLAYER_STATS = ["pass_att", "pass_comp", "pass_yds", "pass_td", "pass_int", "sacks_taken", "rush_att", "rush_yds",
                "rush_td", "targets", "rec", "rec_yds", "rec_td", "rec_20", "tackles", "tfl", "def_sacks", "def_int",
                "pbu", "ff", "fg_att", "fg_made", "xp_made", "punts", "kr", "kr_yds", "pr", "pr_yds"]
TEAM_STATS = ["points", "plays", "dropbacks", "pass_att", "pass_comp", "pass_yds", "rush_att", "rush_yds", "sacks",
              "turnovers", "drives", "punts", "fg_att", "fg_made", "td", "first_downs", "penalties", "total_yds"]


def _logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _sig(x):
    return 1.0 / (1.0 + np.exp(-x))


@dataclass
class Side:
    """One team's pregame parameters. Logit offsets are relative to league rates."""
    team: str
    qb: str | None
    receivers: list = field(default_factory=list)   # (player_id, target_share, catch_logit, ypr_mult)
    rushers: list = field(default_factory=list)     # (player_id, carry_share, ypc_mult)
    defenders: list = field(default_factory=list)   # (player_id, tackle, tfl, sack, int, pbu, ff) shares
    kicker: str | None = None
    punter: str | None = None
    kick_returner: str | None = None
    punt_returner: str | None = None
    pass_offset: float = 0.0
    off: dict = field(default_factory=dict)   # sack, scramble, comp, int, fumble (logit offsets); comp_yds, rush_yds (mult)
    deff: dict = field(default_factory=dict)  # same keys, allowed
    fg_offset: float = 0.0
    eff: float = 1.0                          # efficiency scalar (calibrated to the game model)


@dataclass
class GameSpec:
    season: int
    game_type: str
    home: Side
    away: Side
    league: League


def ot_mode(season: int, game_type: str) -> tuple[str, int]:
    """(mode, period seconds). Modes: 'sd' sudden death, 'sd_td' modified, 'both' both teams possess."""
    playoff = game_type != "REG"
    if playoff:
        return ("both" if season >= 2022 else ("sd_td" if season >= 2010 else "sd")), 900
    period = 600 if season >= 2017 else 900
    if season >= 2025:
        return "both", period
    return ("sd_td" if season >= 2012 else "sd"), period


class Sim:
    def __init__(self, spec: GameSpec, n: int, seed: int = 0):
        self.s, self.n, self.rng = spec, n, np.random.default_rng(seed)
        self.lg = spec.league
        self.sides = [spec.home, spec.away]
        # player registry
        ids = []
        for side in self.sides:
            ids += [side.qb] + [r[0] for r in side.receivers] + [r[0] for r in side.rushers] + [d[0] for d in side.defenders]
            ids += [side.kicker, side.punter, side.kick_returner, side.punt_returner]
        self.players = [p for p in dict.fromkeys(i for i in ids if i)]
        self.pidx = {p: i for i, p in enumerate(self.players)}
        P = max(1, len(self.players))
        self.ps = {k: np.zeros((n, P), np.int32) for k in PLAYER_STATS}
        self.ts = {k: np.zeros((n, 2), np.int32) for k in TEAM_STATS}
        self._prep()

    # ---------------------------------------------------------------- parameters
    def _prep(self):
        lg, r = self.lg, self.lg.rates
        self.base = {k: _logit(r[k]) for k in ("sack", "scramble", "complete", "int", "fumble_lost_run",
                                                "fumble_lost_comp", "pen_off", "pen_def")}
        self.rec_cum, self.rec_pid, self.rec_catch, self.rec_ypr = [], [], [], []
        self.rush_cum, self.rush_pid, self.rush_ypc = [], [], []
        self.def_cum, self.def_pid = [], []
        R = max(2, max(len(s.receivers) for s in self.sides) + 1)
        U = max(2, max(len(s.rushers) for s in self.sides) + 1)
        D = max(2, max(len(s.defenders) for s in self.sides) + 1)
        for s in self.sides:
            sh = np.array([x[1] for x in s.receivers] + [0.0], float)
            tot = sh[:-1].sum()
            untargeted = r["untargeted"]
            sh = sh / tot * (1 - untargeted) if tot > 0 else sh
            sh[-1] = untargeted if tot > 0 else 1.0
            self.rec_cum.append(np.pad(np.cumsum(sh), (0, R - len(sh)), constant_values=1.0))
            self.rec_pid.append(np.pad(np.array([self.pidx[x[0]] for x in s.receivers] + [-1]), (0, R - len(sh)), constant_values=-1))
            self.rec_catch.append(np.pad(np.array([x[2] for x in s.receivers] + [0.0]), (0, R - len(sh))))
            self.rec_ypr.append(np.pad(np.array([x[3] for x in s.receivers] + [1.0]), (0, R - len(sh)), constant_values=1.0))
            rs = np.array([x[1] for x in s.rushers] + [0.0], float)
            rt = rs[:-1].sum()
            rs = rs / rt if rt > 0 else rs
            if rt == 0:
                rs[-1] = 1.0
            self.rush_cum.append(np.pad(np.cumsum(rs), (0, U - len(rs)), constant_values=1.0))
            self.rush_pid.append(np.pad(np.array([self.pidx[x[0]] for x in s.rushers] + [-1]), (0, U - len(rs)), constant_values=-1))
            self.rush_ypc.append(np.pad(np.array([x[2] for x in s.rushers] + [1.0]), (0, U - len(rs)), constant_values=1.0))
            dc, dp = [], []
            for j in range(6):
                v = np.array([d[1 + j] for d in s.defenders] + [0.0], float)
                t = v[:-1].sum()
                v = v / t if t > 0 else v
                if t == 0:
                    v[-1] = 1.0
                dc.append(np.pad(np.cumsum(v), (0, D - len(v)), constant_values=1.0))
            self.def_cum.append(np.stack(dc))
            self.def_pid.append(np.pad(np.array([self.pidx[d[0]] for d in s.defenders] + [-1]), (0, D - len(s.defenders) - 1), constant_values=-1))
        self.rec_cum, self.rec_pid = np.stack(self.rec_cum), np.stack(self.rec_pid)
        self.rec_catch, self.rec_ypr = np.stack(self.rec_catch), np.stack(self.rec_ypr)
        self.rush_cum, self.rush_pid, self.rush_ypc = np.stack(self.rush_cum), np.stack(self.rush_pid), np.stack(self.rush_ypc)
        self.def_cum, self.def_pid = np.stack(self.def_cum), np.stack(self.def_pid)
        self.qb_pid = np.array([self.pidx.get(s.qb, -1) for s in self.sides])
        self.k_pid = np.array([self.pidx.get(s.kicker, -1) for s in self.sides])
        self.p_pid = np.array([self.pidx.get(s.punter, -1) for s in self.sides])
        self.kr_pid = np.array([self.pidx.get(s.kick_returner, -1) for s in self.sides])
        self.pr_pid = np.array([self.pidx.get(s.punt_returner, -1) for s in self.sides])
        def pair(key, mult=False):
            # matrix[o]: offense o vs defense 1-o
            a = np.zeros(2)
            for o in (0, 1):
                d = 1 - o
                if mult:
                    a[o] = self.sides[o].off.get(key, 1.0) * self.sides[d].deff.get(key, 1.0)
                else:
                    a[o] = self.sides[o].off.get(key, 0.0) + self.sides[d].deff.get(key, 0.0)
            return a
        self.m_sack, self.m_scr, self.m_comp, self.m_int = pair("sack"), pair("scramble"), pair("comp"), pair("int")
        self.m_fum = pair("fumble")
        self.m_cy, self.m_ry = pair("comp_yds", True), pair("rush_yds", True)
        self.pass_off = np.array([s.pass_offset for s in self.sides])
        self.fg_off = np.array([s.fg_offset for s in self.sides])
        self.eff = np.array([s.eff for s in self.sides])
        d = lg.dists
        self.punt_bins = {b: (v if len(v) else np.array([40.0], np.float32)) for b, v in d["punt_net_by_yl"].items()}

    # ---------------------------------------------------------------- helpers
    def _draw(self, arr, k):
        return arr[self.rng.integers(0, len(arr), k)] if len(arr) else np.zeros(k, np.float32)

    def _cat(self, cum, o):
        """Categorical draw per sim from cumulative share rows cum[o]."""
        u = self.rng.random(len(o))
        return (u[:, None] > cum[o]).sum(axis=1).clip(max=cum.shape[1] - 1)

    def _add(self, stat, sims, pids, vals=1):
        ok = pids >= 0
        if np.any(ok):
            v = vals[ok] if isinstance(vals, np.ndarray) else vals
            np.add.at(self.ps[stat], (sims[ok], pids[ok]), v)

    def _team(self, stat, sims, team, vals=1):
        np.add.at(self.ts[stat], (sims, team), vals)

    # ---------------------------------------------------------------- run
    def run(self, max_steps: int = 400):
        n, rng, lg = self.n, self.rng, self.lg
        self.q = np.ones(n, np.int16)
        self.clock = np.full(n, 900.0)
        self.score = np.zeros((n, 2), np.int32)
        self.done = np.zeros(n, bool)
        self.opening = rng.integers(0, 2, n)  # team receiving opening kickoff
        self.poss = self.opening.copy()
        self.yl = np.zeros(n)
        self.down = np.ones(n, np.int16)
        self.togo = np.full(n, 10.0)
        self.ot_comp = np.zeros((n, 2), np.int8)
        self.overtime = np.zeros(n, bool)
        self.mode, self.ot_len = ot_mode(self.s.season, self.s.game_type)
        self._kickoff(np.arange(n), self.poss)
        steps = 0
        while not self.done.all() and steps < max_steps:
            self._step(np.nonzero(~self.done)[0])
            steps += 1
        self.steps = steps
        return self

    def _kickoff(self, sims, receiver):
        """Receiver gets ball at empirical start; return stats attributed to returner."""
        k = len(sims)
        if not k:
            return
        self.poss[sims] = receiver
        self.yl[sims] = np.clip(self._draw(self.lg.dists["ko_start"], k), 1, 99) if len(self.lg.dists["ko_start"]) else 70
        self.down[sims], self.togo[sims] = 1, 10
        self._team("drives", sims, receiver)
        ret = self.rng.random(k) < self.lg.rates["ko_returned"]
        if ret.any():
            s2 = sims[ret]
            self._add("kr", s2, self.kr_pid[receiver[ret]])
            self._add("kr_yds", s2, self.kr_pid[receiver[ret]], np.round(self._draw(self.lg.dists["ko_return_yards"], ret.sum())).astype(np.int32))

    def _change(self, sims, new_poss, new_yl):
        """Possession change at a spot (turnover, punt, downs, missed FG)."""
        if not len(sims):
            return
        old = self.poss[sims]
        self._ot_possession_end(sims, old)
        live = ~self.done[sims]
        sims, new_poss, new_yl = sims[live], new_poss[live], new_yl[live]
        self.poss[sims] = new_poss
        self.yl[sims] = np.clip(new_yl, 1, 99)
        self.down[sims], self.togo[sims] = 1, np.minimum(10, self.yl[sims])
        self._team("drives", sims, new_poss)

    def _ot_possession_end(self, sims, team):
        ot = self.overtime[sims]
        if not ot.any():
            return
        s = sims[ot]
        t = team[ot]
        self.ot_comp[s, t] += 1
        if self.mode in ("sd_td", "both"):
            both = (self.ot_comp[s, 0] >= 1) & (self.ot_comp[s, 1] >= 1)
            untied = self.score[s, 0] != self.score[s, 1]
            self.done[s[both & untied]] = True

    def _score(self, sims, team, pts, kind):
        """Add points; resolve overtime end conditions. kind: 'td', 'fg', 'safety'."""
        if not len(sims):
            return
        np.add.at(self.score, (sims, team), pts)
        self._team("points", sims, team, pts)
        ot = self.overtime[sims]
        if ot.any():
            s, t = sims[ot], team[ot]
            other = 1 - t
            ahead = self.score[s, t] > self.score[s, other]
            if self.mode == "sd":
                end = np.ones(len(s), bool)
            elif self.mode == "sd_td":
                first = (self.ot_comp[s, 0] + self.ot_comp[s, 1]) == 0
                end = (first & (kind in ("td", "safety"))) | (~first & ahead)
            else:
                end = (self.ot_comp[s, other] >= 1) & ahead
            self.done[s[end]] = True

    def _after_td(self, sims, team):
        """XP or two-point try, then kickoff to the other team."""
        if not len(sims):
            return
        k = len(sims)
        lg = self.lg
        go2 = self.rng.random(k) < lg.two_pt_rate
        good2 = go2 & (self.rng.random(k) < lg.two_pt_success)
        xp = ~go2 & (self.rng.random(k) < lg.xp_rate)
        pts = 2 * good2 + 1 * xp
        live = ~self.done[sims]
        if live.any():
            self._add("xp_made", sims[live & xp], self.k_pid[team[live & xp]])
            np.add.at(self.score, (sims[live], team[live]), pts[live])
            np.add.at(self.ts["points"], (sims[live], team[live]), pts[live])
            # a try can decide 'both' mode OT only via later possessions; handled at possession end
        recv = 1 - team
        self._ot_possession_end(sims, self.poss[sims])
        live = ~self.done[sims]
        self._kickoff(sims[live], recv[live])

    def _advance_clock(self, sims, cat, hurry):
        dt = np.zeros(len(sims))
        for c in ("run", "sack", "scramble", "complete", "incomplete"):
            for h in (0, 1):
                m = (cat == c) & (hurry == h)
                if m.any():
                    dt[m] = self._draw(self.lg.clock[(c, h)], m.sum())
        self.clock[sims] -= dt
        self._quarter(sims)

    def _quarter(self, sims):
        exp = sims[(self.clock[sims] <= 0) & ~self.done[sims]]
        if not len(exp):
            return
        q = self.q[exp]
        for qq in (1, 3):
            m = exp[q == qq]
            self.q[m] += 1
            self.clock[m] = 900.0
        half = exp[q == 2]
        if len(half):
            self.q[half] = 3
            self.clock[half] = 900.0
            self._ot_possession_end(half, self.poss[half])
            self._kickoff(half, 1 - self.opening[half])
        reg_end = exp[q == 4]
        if len(reg_end):
            tied = self.score[reg_end, 0] == self.score[reg_end, 1]
            self.done[reg_end[~tied]] = True
            ot = reg_end[tied]
            if len(ot):
                self.q[ot] = 5
                self.clock[ot] = float(self.ot_len)
                self.overtime[ot] = True
                self._kickoff(ot, self.rng.integers(0, 2, len(ot)))
        ot_end = exp[q == 5]
        if len(ot_end):
            if self.s.game_type == "REG":
                self.done[ot_end] = True
            else:
                self.clock[ot_end] = 900.0

    def _step(self, a):
        lg, rng = self.lg, self.rng
        o = self.poss[a]
        d = 1 - o
        sd = self.score[a, o] - self.score[a, d]
        q = self.q[a]
        hsr = np.where(np.isin(q, [1, 3]), self.clock[a] + 900, self.clock[a])
        gsr = np.where(q <= 4, (4 - q) * 900 + self.clock[a], self.clock[a])
        hurry = (np.isin(q, [2, 4]) & (hsr <= 240) & (sd <= 0)).astype(int)
        # kneel-out: leading in Q4 with clock less than remaining downs × 40s
        kneel = (q == 4) & (sd > 0) & (self.clock[a] <= 40.0 * (5 - self.down[a]) - 2)
        kneel |= (q == 2) & (self.clock[a] <= 25) & (self.yl[a] >= 60) & (sd >= 0)
        fourth = (self.down[a] == 4) & ~kneel
        kind = np.full(len(a), 0, np.int8)  # 0 run,1 pass,2 fg,3 punt,4 kneel
        kind[kneel] = 4
        if fourth.any():
            idx = np.nonzero(fourth)[0]
            X = fourth_design(self.togo[a][idx], self.yl[a][idx], sd[idx], gsr[idx])
            z = X @ lg.fourth_coef.T + lg.fourth_intercept
            pz = np.exp(z - z.max(1, keepdims=True))
            pz /= pz.sum(1, keepdims=True)
            u = rng.random(len(idx))
            choice = (u[:, None] > np.cumsum(pz, 1)).sum(1)
            dist = self.yl[a][idx] + 17
            choice = np.where((choice == 1) & (dist > 66), 2, choice)
            # final-possession desperation: trailing late must go for it
            late = (gsr[idx] < 180) & (sd[idx] < 0) & ((sd[idx] < -3) | (choice == 2))
            choice = np.where(late, 0, choice)
            go = idx[choice == 0]
            kind[idx[choice == 1]] = 2
            kind[idx[choice == 2]] = 3
            fourth_go = go
        else:
            fourth_go = np.array([], int)
        scrim = np.nonzero((kind == 0) & ~kneel)[0]
        if len(scrim):
            X = pass_design(self.down[a][scrim], self.togo[a][scrim], self.yl[a][scrim], sd[scrim], hsr[scrim], q[scrim])
            p = _sig(X @ lg.pass_coef + lg.pass_intercept + self.pass_off[o[scrim]])
            kind[scrim[rng.random(len(scrim)) < p]] = 1
        self._kneel(a[kind == 4], o[kind == 4])
        self._fg(a[kind == 2], o[kind == 2])
        self._punt(a[kind == 3], o[kind == 3])
        self._scrimmage(a[(kind == 0) | (kind == 1)], o[(kind == 0) | (kind == 1)], kind[(kind == 0) | (kind == 1)],
                        hurry[(kind == 0) | (kind == 1)])

    def _kneel(self, s, o):
        if not len(s):
            return
        self._team("plays", s, o)
        self.yl[s] = np.minimum(self.yl[s] + 1, 99)
        self.down[s] += 1
        self.togo[s] += 1
        self.clock[s] -= np.minimum(self.clock[s], 42.0)
        self._add("rush_att", s, self.qb_pid[o])
        self._add("rush_yds", s, self.qb_pid[o], -1)
        self._team("rush_att", s, o)
        self._team("rush_yds", s, o, -1)
        self._team("total_yds", s, o, -1)
        over = s[self.down[s] > 4]
        self._change(over, 1 - self.poss[over], 100 - self.yl[over])
        self._quarter(s)

    def _fg(self, s, o):
        if not len(s):
            return
        a, b, c = self.lg.fg_coef
        dist = self.yl[s] + 17
        p = _sig(a + b * dist + c * dist ** 2 / 100 + self.fg_off[o])
        made = self.rng.random(len(s)) < p
        self._team("fg_att", s, o)
        self._team("fg_made", s[made], o[made])
        self._add("fg_att", s, self.k_pid[o])
        self._add("fg_made", s[made], self.k_pid[o[made]])
        self.clock[s] -= 5
        self._score(s[made], o[made], 3, "fg")
        m = s[made]
        self._ot_possession_end(m, o[made])
        live = ~self.done[m]
        self._kickoff(m[live], 1 - o[made][live])
        miss = s[~made]
        self._change(miss, 1 - o[~made], 100 - np.maximum(self.yl[miss] + 7, 20))
        self._quarter(s)

    def _punt(self, s, o):
        if not len(s):
            return
        self._team("punts", s, o)
        self._add("punts", s, self.p_pid[o])
        b = np.clip((self.yl[s] // 10).astype(int), 3, 9)
        net = np.zeros(len(s))
        for bb in np.unique(b):
            m = b == bb
            net[m] = self._draw(self.punt_bins[int(bb)], m.sum())
        spot = self.yl[s] - net
        new_yl = np.where(spot <= 0, 80, 100 - spot)
        ret = self.rng.random(len(s)) < self.lg.rates["punt_returned"]
        rcv = 1 - o
        if ret.any():
            self._add("pr", s[ret], self.pr_pid[rcv[ret]])
            self._add("pr_yds", s[ret], self.pr_pid[rcv[ret]], np.round(self._draw(self.lg.dists["punt_return_yards"], ret.sum())).astype(np.int32))
        self.clock[s] -= 8
        self._change(s, rcv, new_yl)
        self._quarter(s)

    def _scrimmage(self, s, o, kind, hurry):
        if not len(s):
            return
        lg, rng, k = self.lg, self.rng, len(s)
        d = 1 - o
        self._team("plays", s, o)
        # penalties (replace the play)
        u = rng.random(k)
        pen_o = u < _sig(self.base["pen_off"])
        pen_d = ~pen_o & (u < _sig(self.base["pen_off"]) + _sig(self.base["pen_def"]))
        gain = np.zeros(k)
        cat = np.where(kind == 1, "incomplete", "run").astype(object)
        first = np.zeros(k, bool)
        to = np.zeros(k, bool)          # turnover
        to_yl = np.zeros(k)
        carrier = np.full(k, -1, np.int64)   # ball-carrier player index for TD credit
        if pen_o.any():
            y = self._draw(lg.dists["pen_off_yards"], pen_o.sum())
            gain[pen_o] = -np.minimum(y, np.maximum(1, (100 - self.yl[s][pen_o]) / 2))
            self._team("penalties", s[pen_o], o[pen_o])
        if pen_d.any():
            y = self._draw(lg.dists["pen_def_yards"], pen_d.sum())
            # half-the-distance enforcement: a penalty can never carry the ball into the end zone
            gain[pen_d] = np.minimum(np.minimum(y, np.maximum(1, self.yl[s][pen_d] / 2)), np.maximum(self.yl[s][pen_d] - 1, 0))
            first[pen_d] = (gain[pen_d] >= self.togo[s][pen_d]) | (y >= 15)
            self._team("penalties", s[pen_d], d[pen_d])
        live = ~pen_o & ~pen_d
        # ---- dropbacks
        db = live & (kind == 1)
        if db.any():
            i = np.nonzero(db)[0]
            ss, oo = s[i], o[i]
            self._team("dropbacks", ss, oo)
            sack = rng.random(len(i)) < _sig(self.base["sack"] + self.m_sack[oo])
            scr = ~sack & (rng.random(len(i)) < _sig(self.base["scramble"] + self.m_scr[oo]))
            thr = ~sack & ~scr
            if sack.any():
                j = i[sack]
                y = self._draw(lg.dists["sack_yards"], sack.sum())
                gain[j] = np.maximum(y, -(100 - self.yl[s[j]]))
                cat[j] = "sack"
                self._team("sacks", s[j], o[j])
                self._add("sacks_taken", s[j], self.qb_pid[o[j]])
                di = self._cat(self.def_cum[:, 2], d[j])
                self._add("def_sacks", s[j], self.def_pid[d[j], di])
                fl = rng.random(len(j)) < lg.rates["fumble_lost_sack"]
                if fl.any():
                    to[j[fl]] = True
                    to_yl[j[fl]] = 100 - (self.yl[s[j[fl]]] - gain[j[fl]])
                    fi = self._cat(self.def_cum[:, 5], d[j[fl]])
                    self._add("ff", s[j[fl]], self.def_pid[d[j[fl]], fi])
            if scr.any():
                j = i[scr]
                y = np.round(self._draw(lg.dists["scramble_yards"], scr.sum()) * self.eff[o[j]])
                gain[j] = np.minimum(y, self.yl[s[j]])
                cat[j] = "scramble"
                carrier[j] = self.qb_pid[o[j]]
                self._add("rush_att", s[j], self.qb_pid[o[j]])
                self._add("rush_yds", s[j], self.qb_pid[o[j]], gain[j].astype(np.int32))
                self._team("rush_att", s[j], o[j])
                self._team("rush_yds", s[j], o[j], gain[j].astype(np.int32))
            if thr.any():
                j = i[thr]
                sj, oj, dj = s[j], o[j], d[j]
                self._team("pass_att", sj, oj)
                self._add("pass_att", sj, self.qb_pid[oj])
                r = self._cat(self.rec_cum, oj)
                rp = self.rec_pid[oj, r]
                self._add("targets", sj, rp)
                inter = rng.random(len(j)) < _sig(self.base["int"] + self.m_int[oj])
                comp = ~inter & (rng.random(len(j)) < _sig(self.base["complete"] + self.m_comp[oj] + self.rec_catch[oj, r]))
                comp &= rp >= 0  # untargeted throws (throwaways) are incomplete
                if inter.any():
                    jj = j[inter]
                    air = self._draw(lg.dists["int_air"], inter.sum())
                    ret = self._draw(lg.dists["int_ret"], inter.sum())
                    at = self.yl[s[jj]] - air
                    nyl = np.where(at <= 0, 80, 100 - at - ret)
                    to[jj] = True
                    to_yl[jj] = nyl
                    self._add("pass_int", s[jj], self.qb_pid[o[jj]])
                    di = self._cat(self.def_cum[:, 3], d[jj])
                    self._add("def_int", s[jj], self.def_pid[d[jj], di])
                if comp.any():
                    jj = j[comp]
                    y = self._draw(lg.dists["comp_yards"], comp.sum()) * self.eff[o[jj]] * self.m_cy[o[jj]] * self.rec_ypr[o[jj], r[comp]]
                    y = np.minimum(np.round(y), self.yl[s[jj]])
                    gain[jj] = y
                    cat[jj] = "complete"
                    rpc = rp[comp]
                    carrier[jj] = rpc
                    self._add("rec", s[jj], rpc)
                    self._add("rec_yds", s[jj], rpc, y.astype(np.int32))
                    self._add("rec_20", s[jj], rpc, (y >= 20).astype(np.int32))
                    self._add("pass_comp", s[jj], self.qb_pid[o[jj]])
                    self._add("pass_yds", s[jj], self.qb_pid[o[jj]], y.astype(np.int32))
                    self._team("pass_comp", s[jj], o[jj])
                    self._team("pass_yds", s[jj], o[jj], y.astype(np.int32))
                    tackled = (y < self.yl[s[jj]]) & (rng.random(comp.sum()) < lg.rates["tackled_comp"])
                    self._tackles(s[jj][tackled], d[jj][tackled])
                    fl = (y < self.yl[s[jj]]) & (rng.random(comp.sum()) < _sig(self.base["fumble_lost_comp"] + self.m_fum[o[jj]]))
                    if fl.any():
                        f = jj[fl]
                        to[f] = True
                        to_yl[f] = 100 - (self.yl[s[f]] - gain[f])
                        fi = self._cat(self.def_cum[:, 5], d[f])
                        self._add("ff", s[f], self.def_pid[d[f], fi])
                inc = ~inter & ~comp
                if inc.any():
                    jj = j[inc]
                    pbu = rng.random(inc.sum()) < lg.rates["pbu_per_incomplete"]
                    di = self._cat(self.def_cum[:, 4], d[jj[pbu]])
                    self._add("pbu", s[jj[pbu]], self.def_pid[d[jj[pbu]], di])
        # ---- designed runs
        rn = live & (kind == 0)
        if rn.any():
            j = np.nonzero(rn)[0]
            sj, oj, dj = s[j], o[j], d[j]
            u_ = self._cat(self.rush_cum, oj)
            rp = self.rush_pid[oj, u_]
            y = self._draw(lg.dists["rush_yards"], len(j))
            y = np.round(np.where(y > 0, y * self.eff[oj] * self.m_ry[oj] * self.rush_ypc[oj, u_], y))
            y = np.minimum(y, self.yl[sj])
            y = np.maximum(y, -(100 - self.yl[sj]))
            gain[j] = y
            carrier[j] = rp
            self._add("rush_att", sj, rp)
            self._add("rush_yds", sj, rp, y.astype(np.int32))
            self._team("rush_att", sj, oj)
            self._team("rush_yds", sj, oj, y.astype(np.int32))
            tackled = (y < self.yl[sj]) & (rng.random(len(j)) < lg.rates["tackled_run"])
            self._tackles(sj[tackled], dj[tackled])
            tfl = tackled & (y < 0)
            if tfl.any():
                di = self._cat(self.def_cum[:, 1], dj[tfl])
                self._add("tfl", sj[tfl], self.def_pid[dj[tfl], di])
            fl = (y < self.yl[sj]) & (rng.random(len(j)) < _sig(self.base["fumble_lost_run"] + self.m_fum[oj]))
            if fl.any():
                f = j[fl]
                to[f] = True
                to_yl[f] = 100 - (self.yl[s[f]] - gain[f])
                fi = self._cat(self.def_cum[:, 5], d[f])
                self._add("ff", s[f], self.def_pid[d[f], fi])
        # ---- resolve field position
        new = self.yl[s] - gain
        td = (new <= 0) & ~to
        safety = (new >= 100) & ~to
        scrim_y = np.where(np.isin(cat, ["complete", "run", "scramble", "sack"]) & ~pen_o & ~pen_d, gain, 0)
        self._team("total_yds", s, o, scrim_y.astype(np.int32))
        if td.any():
            t = np.nonzero(td)[0]
            self._team("td", s[t], o[t])
            # attribute TD
            is_comp = cat[t] == "complete"
            if is_comp.any():
                self._add("pass_td", s[t[is_comp]], self.qb_pid[o[t[is_comp]]])
                self._add("rec_td", s[t[is_comp]], carrier[t[is_comp]])
            is_rush = np.isin(cat[t], ["run", "scramble"])
            if is_rush.any():
                self._add("rush_td", s[t[is_rush]], carrier[t[is_rush]])
            self._score(s[t], o[t], 6, "td")
            self._after_td(s[t], o[t])
        if safety.any():
            t = np.nonzero(safety)[0]
            self._score(s[t], d[t], 2, "safety")
            live_s = ~self.done[s[t]]
            self._ot_possession_end(s[t][live_s], o[t][live_s])
            ls = s[t][live_s & ~self.done[s[t]]]
            if len(ls):
                self.poss[ls] = 1 - self.poss[ls]
                self.yl[ls], self.down[ls], self.togo[ls] = 60, 1, 10
                self._team("drives", ls, self.poss[ls])
        tov = np.nonzero(to)[0]
        if len(tov):
            self._team("turnovers", s[tov], o[tov])
            pick6 = to_yl[tov] <= 0
            p6 = tov[pick6]
            if len(p6):
                self._score(s[p6], d[p6], 6, "td")
                self._after_td(s[p6], d[p6])
            nt = tov[~pick6]
            self._change(s[nt], d[nt], to_yl[nt])
        cont = np.nonzero(~td & ~safety & ~to)[0]
        if len(cont):
            sc = s[cont]
            self.yl[sc] = new[cont]
            g = gain[cont]
            fd = (g >= self.togo[sc]) | first[cont]
            self._team("first_downs", sc[fd], o[cont][fd])
            replay = pen_o[cont]
            self.down[sc] = np.where(fd, 1, np.where(replay, self.down[sc], self.down[sc] + 1))
            self.togo[sc] = np.where(fd, np.minimum(10, self.yl[sc]), np.maximum(1, self.togo[sc] - g))
            tod = sc[self.down[sc] > 4]
            if len(tod):
                self._change(tod, 1 - self.poss[tod], 100 - self.yl[tod])
        cats = np.where(pen_o | pen_d, "incomplete", cat).astype(str)
        self._advance_clock(s, cats, hurry)

    def _tackles(self, s, d):
        if not len(s):
            return
        di = self._cat(self.def_cum[:, 0], d)
        self._add("tackles", s, self.def_pid[d, di])
        ast = self.rng.random(len(s)) < self.lg.rates["assist_per_tackle"]
        if ast.any():
            dj = self._cat(self.def_cum[:, 0], d[ast])
            self._add("tackles", s[ast], self.def_pid[d[ast], dj])
