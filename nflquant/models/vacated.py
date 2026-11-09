"""Vacated-usage features: usage left behind by teammates ruled Out/Doubtful.

vac_tgt / vac_car / vac_att = Σ over the team's Out/Doubtful players of their most recent
pregame EWMA share of team targets / carries / pass attempts. Interaction terms multiply by the
player's own share. Inputs are pregame-known (official injury report + prior usage).
Walk-forward evidence (research/vacated_usage_test.py, 2019-2026): role-shock MAE improves
12.8% for QB attempts, 11.6% passing yards, 6.4% carries; ~0.1-0.9% overall.
"""
from __future__ import annotations

import pandas as pd

from .. import lake


def add_vacated_usage(d: pd.DataFrame) -> pd.DataFrame:
    inj = lake.load("injuries")
    out = inj[inj["report_status"].isin(["Out", "Doubtful"])][["season", "week", "team", "gsis_id"]]
    out = out.rename(columns={"gsis_id": "player_id"}).dropna().drop_duplicates()
    hist = d[["season", "week", "team", "player_id", "ew_share_targets", "ew_share_carries", "ew_share_attempts"]]
    m = out.merge(hist, on=["season", "team", "player_id"], suffixes=("", "_h"))
    m = m[m["week_h"] < m["week"]].sort_values("week_h").groupby(["season", "week", "team", "player_id"]).last().reset_index()
    v = m.groupby(["season", "week", "team"]).agg(vac_tgt=("ew_share_targets", "sum"), vac_car=("ew_share_carries", "sum"),
                                                   vac_att=("ew_share_attempts", "sum")).reset_index()
    d = d.drop(columns=[c for c in ("vac_tgt", "vac_car", "vac_att") if c in d]).merge(v, on=["season", "week", "team"], how="left")
    for c in ("vac_tgt", "vac_car", "vac_att"):
        d[c] = d[c].fillna(0.0).clip(0, 1.5)
    d["own_x_vac_tgt"] = d["ew_share_targets"].fillna(0) * d["vac_tgt"]
    d["own_x_vac_car"] = d["ew_share_carries"].fillna(0) * d["vac_car"]
    d["own_x_vac_att"] = d["ew_share_attempts"].fillna(0) * d["vac_att"]
    return d
