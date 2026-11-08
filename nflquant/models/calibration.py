"""Probability calibration and scoring rules."""
from __future__ import annotations

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

EPS = 1e-6


def logit(p):
    p = np.clip(np.asarray(p, float), EPS, 1 - EPS)
    return np.log(p / (1 - p))


class Platt:
    """Logistic recalibration on logit(p). Identity until fitted."""

    def __init__(self):
        self.model = None

    def fit(self, p, y):
        y = np.asarray(y, float)
        keep = (y == 0) | (y == 1)
        if keep.sum() < 100:
            return self
        self.model = LogisticRegression(C=1e3).fit(logit(p)[keep].reshape(-1, 1), y[keep].astype(int))
        return self

    def predict(self, p):
        if self.model is None:
            return np.asarray(p, float)
        return self.model.predict_proba(logit(p).reshape(-1, 1))[:, 1]

    def params(self):
        return None if self.model is None else {"a": float(self.model.coef_[0, 0]), "b": float(self.model.intercept_[0])}


class Isotonic:
    def __init__(self):
        self.model = None

    def fit(self, p, y):
        y = np.asarray(y, float)
        keep = (y == 0) | (y == 1)
        if keep.sum() < 300:
            return self
        self.model = IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.99).fit(np.asarray(p)[keep], y[keep])
        return self

    def predict(self, p):
        return np.asarray(p, float) if self.model is None else self.model.predict(np.asarray(p, float))


def log_loss(p, y) -> float:
    p = np.clip(np.asarray(p, float), EPS, 1 - EPS)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier(p, y) -> float:
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def reliability(p, y, bins: int = 10) -> list[dict]:
    p, y = np.asarray(p, float), np.asarray(y, float)
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi if hi < 1 else p <= hi)
        if m.sum():
            out.append({"bin_lo": round(lo, 2), "bin_hi": round(hi, 2), "n": int(m.sum()),
                        "mean_pred": float(p[m].mean()), "observed": float(y[m].mean())})
    return out


def ece(p, y, bins: int = 10) -> float:
    rel = reliability(p, y, bins)
    n = sum(r["n"] for r in rel)
    return float(sum(r["n"] * abs(r["mean_pred"] - r["observed"]) for r in rel) / n) if n else float("nan")


def interval_coverage(y, lo, hi) -> float:
    y, lo, hi = map(lambda a: np.asarray(a, float), (y, lo, hi))
    return float(np.mean((y >= lo) & (y <= hi)))
