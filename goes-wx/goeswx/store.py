"""SQLite archive of point forecasts and observations, the training data for MOS."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

# Stored in metric: °C, m/s, hPa, %, mm.
FCST_COLS = ["t2m", "d2m", "wspd", "gust", "mslp", "tcc", "precip"]
OBS_COLS = ["t2m", "d2m", "wspd", "gust", "mslp"]

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS forecasts (
    run TEXT NOT NULL, valid TEXT NOT NULL, lead INTEGER NOT NULL,
    {", ".join(f"{c} REAL" for c in FCST_COLS)},
    PRIMARY KEY (run, valid)
);
CREATE TABLE IF NOT EXISTS obs (
    valid TEXT PRIMARY KEY,
    {", ".join(f"{c} REAL" for c in OBS_COLS)},
    source TEXT
);
"""


@contextmanager
def connect(path: Path):
    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def _iso(t) -> str:
    return pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def save_forecasts(path: Path, rows: list[dict]) -> None:
    cols = ["run", "valid", "lead", *FCST_COLS]
    with connect(path) as con:
        con.executemany(
            f"INSERT OR REPLACE INTO forecasts ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [(_iso(r["run"]), _iso(r["valid"]), int(r["lead"]), *(r.get(c) for c in FCST_COLS))
             for r in rows])


def save_obs(path: Path, rows: list[dict], source: str) -> None:
    cols = ["valid", *OBS_COLS, "source"]
    with connect(path) as con:
        con.executemany(
            f"INSERT OR REPLACE INTO obs ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [(_iso(r["valid"]), *(r.get(c) for c in OBS_COLS), source) for r in rows])


def load_pairs(path: Path, tolerance_min: int = 35) -> pd.DataFrame:
    """Forecasts joined to the nearest observation (within tolerance) at their valid time."""
    with connect(path) as con:
        f = pd.read_sql("SELECT * FROM forecasts", con, parse_dates=["run", "valid"])
        o = pd.read_sql("SELECT * FROM obs", con, parse_dates=["valid"])
    if f.empty or o.empty:
        return pd.DataFrame()
    f = f.sort_values("valid")
    o = o.sort_values("valid").drop(columns=["source"]).add_prefix("obs_")
    return pd.merge_asof(f, o, left_on="valid", right_on="obs_valid",
                         tolerance=pd.Timedelta(minutes=tolerance_min), direction="nearest")


def counts(path: Path) -> dict:
    with connect(path) as con:
        return {
            "forecast_rows": con.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0],
            "forecast_runs": con.execute("SELECT COUNT(DISTINCT run) FROM forecasts").fetchone()[0],
            "obs_rows": con.execute("SELECT COUNT(*) FROM obs").fetchone()[0],
        }
