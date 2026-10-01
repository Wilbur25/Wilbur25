"""Build the multi-day point forecast: GFS at your location, corrected by the local MOS model."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from . import gfs, mos, store, units  # noqa: E402
from .config import Config  # noqa: E402
from .solar import solar_zenith  # noqa: E402

log = logging.getLogger(__name__)

RAIN_MM = 0.25   # precip in a step below this is ignored


def condition(row, night: bool) -> tuple[str, str]:
    """(text, icon) for one forecast step."""
    p, tcc = row.get("precip", 0) or 0, row.get("tcc")
    if p >= RAIN_MM:
        if (row.get("cape") or 0) > 800 and (row.get("csnow") or 0) < 0.5:
            return "Thunderstorms", "⛈️"
        if (row.get("csnow") or 0) >= 0.5:
            return "Snow", "🌨️"
        return ("Heavy rain", "🌧️") if p >= 6 else ("Rain", "🌦️" if (tcc or 100) < 80 else "🌧️")
    if tcc is None or np.isnan(tcc):
        return "", ""
    if tcc < 20:
        return ("Clear", "🌙") if night else ("Sunny", "☀️")
    if tcc < 50:
        return ("Mostly clear", "🌙") if night else ("Mostly sunny", "🌤️")
    if tcc < 85:
        return "Partly cloudy", "☁️" if night else "⛅"
    return "Cloudy", "☁️"


def clean(obj):
    """Replace NaN with None recursively so the JSON is valid for browsers."""
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean(v) for v in obj]
    if isinstance(obj, float) and not np.isfinite(obj):
        return None
    return obj


def to_frame(results: list[dict], run: datetime, lat: float, lon: float) -> pd.DataFrame:
    rows = []
    for r in results:
        p = r["point"]
        u, v = p.get("u10", np.nan), p.get("v10", np.nan)
        rows.append({
            "run": run, "valid": r["valid"], "lead": r["step"],
            "t2m": p.get("t2m", np.nan) - 273.15, "d2m": p.get("d2m", np.nan) - 273.15,
            "tmax": p.get("tmax", np.nan) - 273.15, "tmin": p.get("tmin", np.nan) - 273.15,
            "rh": p.get("rh", np.nan),
            "wspd": float(np.hypot(u, v)), "wdir": float((np.degrees(np.arctan2(-u, -v))) % 360),
            "gust": p.get("gust", np.nan), "mslp": p.get("mslp", np.nan) / 100,
            "tcc": p.get("tcc", np.nan), "apcp": p.get("apcp", 0.0 if r["step"] == 0 else np.nan),
            "crain": p.get("crain", np.nan), "csnow": p.get("csnow", np.nan),
            "cape": p.get("cape", np.nan),
        })
    df = pd.DataFrame(rows).sort_values("lead").reset_index(drop=True)
    df["apcp"] = df["apcp"].interpolate(limit_direction="forward").fillna(0)
    df["precip"] = df["apcp"].diff().fillna(0).clip(lower=0)
    # f000 has no cloud/accumulation fields; borrow from the next step.
    df["tcc"] = df["tcc"].bfill()
    df["night"] = [bool(solar_zenith(lat, lon, t.to_pydatetime()) > 90) for t in df["valid"]]
    return df


def daily(df: pd.DataFrame, tz) -> list[dict]:
    d = df.copy()
    d["local"] = d["valid"].dt.tz_convert(tz)
    # A step's precip and max/min windows cover the hours *before* its valid time.
    d["day"] = (d["local"] - pd.Timedelta(minutes=1)).dt.date
    out = []
    for day, g in d.groupby("day"):
        if g["lead"].max() == 0:
            continue
        high = np.nanmax(np.concatenate([g["tmax"].values, g["t2m"].values]))
        low = np.nanmin(np.concatenate([g["tmin"].values, g["t2m"].values]))
        precip = float(g["precip"].sum())
        dayt = g[~g["night"]]
        tcc = float(dayt["tcc"].mean()) if len(dayt) else float(g["tcc"].mean())
        wet = g[g["precip"] >= RAIN_MM]
        if precip >= 1.0 and len(wet):
            stormy = ((wet["cape"] > 800) & (wet["csnow"] < 0.5)).any()
            snowy = (wet["csnow"] >= 0.5).mean() > 0.5
            text, icon = ("Thunderstorms", "⛈️") if stormy else ("Snow", "🌨️") if snowy else ("Rain", "🌧️")
        else:
            text, icon = condition({"tcc": tcc, "precip": 0}, night=False)
        out.append({
            "date": str(day), "weekday": pd.Timestamp(day).strftime("%a"),
            "high_c": round(float(high), 1), "low_c": round(float(low), 1),
            "precip_mm": round(precip, 1), "max_gust_ms": round(float(g["gust"].max()), 1),
            "cloud_pct": round(tcc), "condition": text, "icon": icon,
            "partial": bool(g["local"].min().hour > 3) if day == d["day"].min() else False,
        })
    return out


def meteogram(df: pd.DataFrame, out: Path, cfg: Config, corrected: list[str]) -> None:
    imp = cfg.imperial
    u = units.labels(imp)
    tz = cfg.tz
    t = df["valid"].dt.tz_convert(tz).dt.tz_localize(None)
    T = lambda c: c * 9 / 5 + 32 if imp else c  # noqa: E731
    S = lambda s: s * 2.23694 if imp else s * 3.6  # noqa: E731
    P = lambda p: p / 25.4 if imp else p  # noqa: E731

    plt.style.use("dark_background")
    fig, axes = plt.subplots(4, 1, figsize=(12, 9), sharex=True,
                             gridspec_kw={"height_ratios": [3, 2, 2, 1.5], "hspace": 0.08})
    fig.patch.set_facecolor("#0f1720")
    for ax in axes:
        ax.set_facecolor("#0f1720")
        ax.grid(alpha=0.15)
        for day in pd.date_range(t.min().normalize(), t.max(), freq="D")[1:]:
            ax.axvline(day, color="white", alpha=0.25, lw=0.8)
        night = df["night"].values
        for i in range(len(t) - 1):
            if night[i]:
                ax.axvspan(t.iloc[i], t.iloc[i + 1], color="black", alpha=0.35, lw=0)

    ax = axes[0]
    ax.plot(t, T(df["t2m"]), color="#ff6b4a", lw=2, label="Temperature")
    ax.plot(t, T(df["d2m"]), color="#4ac6ff", lw=1.5, label="Dew point")
    if "t2m" in corrected:
        ax.plot(t, T(df["t2m_gfs"]), color="#ff6b4a", lw=1, ls="--", alpha=0.6, label="Raw GFS temp")
    ax.set_ylabel(u["temp"])
    ax.legend(loc="upper left", fontsize=8, ncol=3, frameon=False)

    ax = axes[1]
    width = (df["lead"].diff().median() or 3) / 24 * 0.9
    ax.bar(t - pd.Timedelta(hours=float(width * 12)), P(df["precip"]), width=width,
           color="#3fa9f5", label=f"Precipitation ({u['precip']}/step)")
    ax.set_ylabel(u["precip"])
    ax2 = ax.twinx()
    ax2.fill_between(t, df["tcc"], color="white", alpha=0.12, step="mid")
    ax2.set_ylim(0, 100)
    ax2.set_ylabel("Cloud %", color="#aaa")
    ax.set_ylim(bottom=0, top=max(0.1 if imp else 2, float(P(df["precip"]).max()) * 1.2))
    ax.legend(loc="upper left", fontsize=8, frameon=False)

    ax = axes[2]
    ax.plot(t, S(df["wspd"]), color="#9be564", lw=1.8, label="Wind")
    ax.plot(t, S(df["gust"]), color="#9be564", lw=1, ls=":", label="Gusts")
    step = max(1, len(df) // 40)
    sub = df.iloc[::step]
    ts = t.iloc[::step]
    ymax = float(S(df["gust"]).max() or 10)
    # Arrows point the way the wind blows *to*.
    ax.quiver(ts, np.full(len(sub), ymax * 1.15), -np.sin(np.radians(sub["wdir"])),
              -np.cos(np.radians(sub["wdir"])), color="#cfe8c0", scale=70, width=0.0018,
              headwidth=4, pivot="middle")
    ax.set_ylim(0, ymax * 1.3)
    ax.set_ylabel(u["speed"])
    ax.legend(loc="upper left", bbox_to_anchor=(0, 0.86), fontsize=8, ncol=2, frameon=False)

    ax = axes[3]
    pres = df["mslp"] * 0.02953 if imp else df["mslp"]
    ax.plot(t, pres, color="#d7a8ff", lw=1.5)
    ax.set_ylabel(u["pressure"])
    ax.xaxis.set_major_locator(mdates.DayLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %d"))
    ax.xaxis.set_minor_locator(mdates.HourLocator(byhour=[6, 12, 18]))

    run = df["run"].iloc[0]
    src = "GFS + local correction" if corrected else "GFS"
    fig.suptitle(f"{cfg['location']['name']} — {src}, run {run:%Y-%m-%d %HZ}",
                 x=0.01, ha="left", fontsize=12, y=0.93)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=100, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


def _region_grids(results: list[dict], tz) -> dict:
    """Per local day: max temperature and total precipitation grids for the map region."""
    lats, lons = None, None
    by_day: dict = {}
    prev_apcp = None
    for r in results:
        g = r["grids"]
        if "t2m" not in g:
            continue
        lats, lons = g["t2m"][0], g["t2m"][1]
        day = (pd.Timestamp(r["valid"]).tz_convert(tz) - pd.Timedelta(minutes=1)).date()
        e = by_day.setdefault(str(day), {"tmax": None, "precip": np.zeros_like(g["t2m"][2])})
        hot = g["tmax"][2] if "tmax" in g else g["t2m"][2]
        hot = np.fmax(hot, g["t2m"][2])
        e["tmax"] = hot if e["tmax"] is None else np.fmax(e["tmax"], hot)
        if "apcp" in g:
            ap = g["apcp"][2]
            if prev_apcp is not None:
                e["precip"] += np.clip(ap - prev_apcp, 0, None)
            prev_apcp = ap
    return {"lats": lats, "lons": lons, "days": by_day}


def run(cfg: Config, force: bool = False) -> dict | None:
    fc = cfg["forecast"]
    loc = cfg["location"]
    steps = gfs.steps_for(fc["days"], fc["step_hours"])
    gfs_run = gfs.latest_run(fc["resolution"], steps[-1])
    out_json = cfg.output_dir / "forecast.json"
    if not force and out_json.exists():
        if json.loads(out_json.read_text()).get("run") == gfs_run.isoformat():
            log.info("GFS %s already processed", gfs_run)
            return None

    log.info("GFS run %s: fetching %d steps", gfs_run, len(steps))
    region_names = gfs.MAP_FIELDS if fc["maps"] else ()
    results = gfs.fetch_run(gfs_run, steps, fc["resolution"], list(gfs.FIELDS), loc["lat"], loc["lon"],
                            cfg["region"] if fc["maps"] else None, region_names)
    if len(results) < len(steps) * 0.8:
        raise RuntimeError(f"only {len(results)}/{len(steps)} GFS steps downloaded")

    df = to_frame(results, gfs_run, loc["lat"], loc["lon"])
    store.save_forecasts(cfg.data_dir / "goeswx.db", df.to_dict("records"))

    df, corrected = mos.apply(cfg, df)
    if "t2m" in corrected:   # shift the window extremes by the same correction
        delta = df["t2m"] - df["t2m_gfs"]
        df["tmax"] += delta
        df["tmin"] += delta

    for col in ("cond", "icon"):
        df[col] = ""
    for i, row in df.iterrows():
        df.at[i, "cond"], df.at[i, "icon"] = condition(row, row["night"])

    days = daily(df, cfg.tz)
    meteogram(df, cfg.output_dir / "meteogram.png", cfg, corrected)

    if fc["maps"]:
        grids = _region_grids(results, cfg.tz)
        if grids["lats"] is not None:
            np.savez_compressed(
                cfg.data_dir / "forecast_grids.npz", lats=grids["lats"], lons=grids["lons"],
                days=np.array(list(grids["days"])),
                tmax=np.stack([v["tmax"] - 273.15 for v in grids["days"].values()]),
                precip=np.stack([v["precip"] for v in grids["days"].values()]),
                run=np.array(gfs_run.isoformat()))

    hourly = []
    for _, r in df.iterrows():
        hourly.append({
            "valid": r["valid"].isoformat(), "lead": int(r["lead"]),
            "t2m_c": round(r["t2m"], 1), "d2m_c": round(r["d2m"], 1),
            "rh": None if np.isnan(r["rh"]) else round(r["rh"]),
            "wspd_ms": round(r["wspd"], 1), "wdir": round(r["wdir"]), "gust_ms": round(r["gust"], 1),
            "mslp_hpa": round(r["mslp"], 1), "cloud_pct": None if np.isnan(r["tcc"]) else round(r["tcc"]),
            "precip_mm": round(r["precip"], 2), "cape": None if np.isnan(r["cape"]) else round(r["cape"]),
            "condition": r["cond"], "icon": r["icon"], "night": bool(r["night"]),
        })
    result = {
        "run": gfs_run.isoformat(),
        "generated": datetime.now(timezone.utc).isoformat(),
        "model": f"GFS {fc['resolution'].replace('p', '.')}°",
        "corrected": corrected,
        "daily": days,
        "hourly": hourly,
    }
    out_json.write_text(json.dumps(clean(result), indent=1))
    log.info("forecast written: %d days, local correction for %s", len(days), corrected or "nothing yet")
    return result


def backfill(cfg: Config, days: int, cycles=(0, 12)) -> None:
    """Archive past GFS point forecasts (and station history) so MOS can train right away."""
    from . import obs

    fc, loc = cfg["forecast"], cfg["location"]
    db = cfg.data_dir / "goeswx.db"
    steps = [s for s in gfs.steps_for(fc["days"], 6)]
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = (now - timedelta(days=days)).replace(hour=0)

    log.info("station history for %s since %s", loc["metar_station"], start.date())
    try:
        rows = obs.history(loc["metar_station"], start - timedelta(days=1), now + timedelta(days=1))
        store.save_obs(db, rows, "iem")
        log.info("saved %d observations", len(rows))
    except Exception as e:  # forecasts are still worth archiving; obs accumulate from METARs
        log.warning("station history download failed: %s", e)

    with store.connect(db) as con:
        have = {r[0] for r in con.execute("SELECT DISTINCT run FROM forecasts")}
    names = gfs.MOS_FIELDS
    t = start
    while t <= now - timedelta(hours=6):
        if t.hour in cycles and t.strftime("%Y-%m-%dT%H:%M:%SZ") not in have:
            log.info("backfilling GFS %s", t)
            res = gfs.fetch_run(t, steps, fc["resolution"], names, loc["lat"], loc["lon"])
            if res:
                store.save_forecasts(db, to_frame(res, t, loc["lat"], loc["lon"]).to_dict("records"))
        t += timedelta(hours=6)
