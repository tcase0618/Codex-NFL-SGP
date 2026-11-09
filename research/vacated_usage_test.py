"""Walk-forward test: do 'vacated usage' features (teammates ruled Out/Doubtful) improve
played-only projections, especially in role-shock games?

Vacated share = Σ over the team's Out/Doubtful players of their most recent EWMA share of team
targets / carries / attempts (pregame-known: official report + prior usage). Features added:
vac_tgt, vac_car, vac_att, and the player's own share × vacated share for each.
"""
import json

import numpy as np
import pandas as pd
from xgboost import XGBRegressor

from nflquant import lake
from nflquant.config import settings
from nflquant.models.player import FEATURES, TARGETS, _X, rows_for

TARGETS_TEST = ["targets", "receptions", "receiving_yards", "carries", "rushing_yards", "attempts", "passing_yards", "passing_tds"]
VAC = ["vac_tgt", "vac_car", "vac_att", "own_x_vac_tgt", "own_x_vac_car", "own_x_vac_att"]


def vacated(d: pd.DataFrame) -> pd.DataFrame:
    inj = lake.load("injuries")
    out = inj[inj["report_status"].isin(["Out", "Doubtful"])][["season", "week", "team", "gsis_id"]].rename(columns={"gsis_id": "player_id"})
    out = out.dropna().drop_duplicates()
    hist = d[["season", "week", "team", "player_id", "ew_share_targets", "ew_share_carries", "ew_share_attempts"]]
    # most recent usage known before the injury week (dataset rows are already pregame states)
    m = out.merge(hist, on=["season", "team", "player_id"], suffixes=("", "_h"))
    m = m[m["week_h"] < m["week"]].sort_values("week_h").groupby(["season", "week", "team", "player_id"]).last().reset_index()
    v = m.groupby(["season", "week", "team"]).agg(vac_tgt=("ew_share_targets", "sum"), vac_car=("ew_share_carries", "sum"),
                                                   vac_att=("ew_share_attempts", "sum")).reset_index()
    d = d.merge(v, on=["season", "week", "team"], how="left")
    for c in ("vac_tgt", "vac_car", "vac_att"):
        d[c] = d[c].fillna(0.0).clip(0, 1.5)
    d["own_x_vac_tgt"] = d["ew_share_targets"].fillna(0) * d["vac_tgt"]
    d["own_x_vac_car"] = d["ew_share_carries"].fillna(0) * d["vac_car"]
    d["own_x_vac_att"] = d["ew_share_attempts"].fillna(0) * d["vac_att"]
    return d


def model(kind):
    common = dict(n_estimators=350, learning_rate=0.05, max_depth=4, min_child_weight=20, subsample=0.8, colsample_bytree=0.8,
                  reg_lambda=5.0, n_jobs=4, random_state=11, tree_method="hist")
    return XGBRegressor(objective="count:poisson", max_delta_step=0.7, **common) if kind == "count" else XGBRegressor(objective="reg:squarederror", **common)


def run(first_test=2019):
    d = pd.read_parquet(settings().lake_dir / "player_dataset.parquet")
    d = d[d["game_id"].isin(set(lake.load("games").query("completed").game_id))]
    d = vacated(d)
    res, rows = {}, []
    for t in TARGETS_TEST:
        kind = TARGETS[t][1]
        r = rows_for(d, t)
        for S in range(first_test, int(d["season"].max()) + 1):
            tr, te = r[r["season"] < S], r[r["season"] == S]
            Xtr, Xte = _X(tr), _X(te)
            base = model(kind).fit(Xtr, tr[f"y_{t}"]).predict(Xte)
            Xtr2, Xte2 = Xtr.assign(**{c: tr[c].to_numpy() for c in VAC}), Xte.assign(**{c: te[c].to_numpy() for c in VAC})
            new = model(kind).fit(Xtr2, tr[f"y_{t}"]).predict(Xte2)
            rows.append(pd.DataFrame({"target": t, "season": S, "y": te[f"y_{t}"].to_numpy(), "base": base, "new": new,
                                      "ewma": te[f"ew_{'passing_yards' if t=='passing_yards' else t}"].fillna(0).to_numpy() if f"ew_{t}" in te else np.nan,
                                      "played": te["played"].to_numpy(), "vac": te[["vac_tgt", "vac_car", "vac_att"]].max(axis=1).to_numpy(),
                                      "vac_rel": np.where(t in ("carries", "rushing_yards"), te["vac_car"], np.where(t in ("attempts", "passing_yards", "passing_tds"), te["vac_att"], te["vac_tgt"]))}))
            print(t, S, flush=True)
    a = pd.concat(rows, ignore_index=True)
    a.to_parquet(settings().lake_dir / "vacated_usage_oos.parquet", index=False)
    for t, g in a.groupby("target"):
        p = g[g["played"] == 1]
        thr = 0.5 if t in ("attempts", "passing_yards", "passing_tds") else (0.25 if t in ("carries", "rushing_yards") else 0.15)
        shock, normal = p[p["vac_rel"] >= thr], p[p["vac_rel"] < 0.02]
        f = lambda s, c: float(np.mean(np.abs(s[c] - s["y"])))
        res[t] = {"n_played": int(len(p)), "mae_base": f(p, "base"), "mae_new": f(p, "new"),
                  "improvement_played": 1 - f(p, "new") / f(p, "base"),
                  "n_shock": int(len(shock)), "mae_shock_base": f(shock, "base"), "mae_shock_new": f(shock, "new"),
                  "improvement_shock": 1 - f(shock, "new") / f(shock, "base") if len(shock) else None,
                  "bias_shock_base": float(np.mean(shock["base"] - shock["y"])), "bias_shock_new": float(np.mean(shock["new"] - shock["y"])),
                  "improvement_all_rows": 1 - f(g, "new") / f(g, "base")}
    (settings().lake_dir / "vacated_usage_test.json").write_text(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    print(json.dumps(run(), indent=1))
