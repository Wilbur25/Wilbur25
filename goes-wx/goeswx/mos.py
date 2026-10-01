"""Local model output statistics (MOS): learn how GFS is wrong at *your* location.

GFS is a ~25 km global model. It misses local effects (terrain, rivers, cities,
the airport's exact siting), and its errors are systematic: too cold on clear
nights, too windy on calm ones, and so on. We train a gradient-boosted model on
(GFS forecast -> what the station actually observed) pairs, and add the predicted
error back to each new forecast. Each target is only used if it beats raw GFS on
held-out runs.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from . import store
from .config import Config

log = logging.getLogger(__name__)

TARGETS = ["t2m", "d2m", "wspd"]
FEATURES = ["lead", "t2m", "d2m", "wspd", "gust", "mslp", "tcc", "precip",
            "hour_sin", "hour_cos", "doy_sin", "doy_cos"]
MIN_PAIRS = 300
MIN_RUNS = 10


def model_path(cfg: Config) -> Path:
    return cfg.data_dir / "mos.joblib"


def features(df: pd.DataFrame, lon: float) -> pd.DataFrame:
    valid = pd.to_datetime(df["valid"], utc=True)
    solar_hour = (valid.dt.hour + valid.dt.minute / 60 + lon / 15) % 24
    doy = valid.dt.dayofyear
    out = df.reindex(columns=["lead", "t2m", "d2m", "wspd", "gust", "mslp", "tcc", "precip"]).astype(float)
    out["hour_sin"] = np.sin(2 * np.pi * solar_hour / 24).values
    out["hour_cos"] = np.cos(2 * np.pi * solar_hour / 24).values
    out["doy_sin"] = np.sin(2 * np.pi * doy / 365.25).values
    out["doy_cos"] = np.cos(2 * np.pi * doy / 365.25).values
    return out[FEATURES]


def _regressor():
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=15,
                                         min_samples_leaf=20, l2_regularization=1.0)


def train(cfg: Config) -> dict:
    db = cfg.data_dir / "goeswx.db"
    pairs = store.load_pairs(db)
    lon = cfg["location"]["lon"]
    report = {"trained": datetime.now(timezone.utc).isoformat(), "targets": {}}
    models = {}
    for target in TARGETS:
        if pairs.empty:
            report["targets"][target] = {"status": "no data yet"}
            continue
        d = pairs.dropna(subset=[target, f"obs_{target}"])
        runs = np.sort(d["run"].unique())
        if len(d) < MIN_PAIRS or len(runs) < MIN_RUNS:
            report["targets"][target] = {
                "status": f"waiting for data ({len(d)}/{MIN_PAIRS} pairs, {len(runs)}/{MIN_RUNS} runs)"}
            continue
        cut = runs[int(len(runs) * 0.8)]
        tr, va = d[d["run"] < cut], d[d["run"] >= cut]
        resid = lambda x: x[f"obs_{target}"] - x[target]  # noqa: E731
        m = _regressor().fit(features(tr, lon), resid(tr))
        raw_mae = float(np.mean(np.abs(resid(va))))
        mos_mae = float(np.mean(np.abs(resid(va) - m.predict(features(va, lon)))))
        info = {"pairs": int(len(d)), "runs": int(len(runs)), "validation_pairs": int(len(va)),
                "mae_gfs": round(raw_mae, 2), "mae_local": round(mos_mae, 2)}
        if mos_mae < raw_mae:
            models[target] = _regressor().fit(features(d, lon), resid(d))   # refit on everything
            info["status"] = "active"
        else:
            info["status"] = "not better than GFS yet; not used"
        report["targets"][target] = info
        log.info("%s: %s", target, info)
    joblib.dump(models, model_path(cfg))
    (cfg.data_dir / "mos_report.json").write_text(json.dumps(report, indent=2))
    return report


def apply(cfg: Config, df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Return df with corrected columns (raw kept as <name>_gfs) and the list of corrected targets."""
    path = model_path(cfg)
    if not path.exists():
        return df, []
    models = joblib.load(path)
    if not models:
        return df, []
    df = df.copy()
    X = features(df, cfg["location"]["lon"])
    for target, m in models.items():
        ok = X[target].notna()
        df[f"{target}_gfs"] = df[target]
        df.loc[ok, target] = df.loc[ok, target] + m.predict(X[ok])
    if "wspd" in models:
        df["wspd"] = df["wspd"].clip(lower=0)
    if "d2m" in models or "t2m" in models:
        df["d2m"] = np.minimum(df["d2m"], df["t2m"])
    return df, sorted(models)
