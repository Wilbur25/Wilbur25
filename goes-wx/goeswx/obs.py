"""Surface observations: live METARs (aviationweather.gov) and history (Iowa Environmental Mesonet)."""

from __future__ import annotations

import io
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

log = logging.getLogger(__name__)

KT_TO_MS = 0.514444
UA = {"User-Agent": "goeswx/0.1 (home weather station)"}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def latest_metar(station: str) -> dict | None:
    """Most recent METAR for an ICAO station, normalised to metric units."""
    r = requests.get("https://aviationweather.gov/api/data/metar",
                     params={"ids": station, "format": "json", "hours": 3}, headers=UA, timeout=30)
    r.raise_for_status()
    if not r.text.strip():
        return None
    data = r.json()
    if not data:
        return None
    m = max(data, key=lambda d: d.get("obsTime") or 0)
    wspd, gust = _num(m.get("wspd")), _num(m.get("wgst"))
    clouds = m.get("clouds") or []
    return {
        "station": m.get("icaoId", station),
        "name": m.get("name"),
        "valid": datetime.fromtimestamp(m["obsTime"], timezone.utc) if m.get("obsTime") else None,
        "t2m": _num(m.get("temp")),
        "d2m": _num(m.get("dewp")),
        "wspd": None if wspd is None else wspd * KT_TO_MS,
        "gust": None if gust is None else gust * KT_TO_MS,
        "wdir": m.get("wdir"),
        "mslp": _num(m.get("slp")) or _num(m.get("altim")),
        "visibility_mi": m.get("visib"),
        "weather": m.get("wxString"),
        "clouds": [f"{c.get('cover')} {c.get('base')}ft" if c.get("base") else c.get("cover")
                   for c in clouds],
        "raw": m.get("rawOb"),
    }


def history(station: str, start: datetime, end: datetime) -> list[dict]:
    """Hourly routine observations from the IEM ASOS archive, metric units."""
    ids = [station] + ([station[1:]] if len(station) == 4 and station[0] == "K" else [])
    for sid in ids:
        params = {
            "station": sid, "data": ["tmpf", "dwpf", "sknt", "gust", "mslp"],
            "year1": start.year, "month1": start.month, "day1": start.day,
            "year2": end.year, "month2": end.month, "day2": end.day,
            "tz": "Etc/UTC", "format": "onlycomma", "latlon": "no", "missing": "M",
            "trace": "0.0001", "direct": "no", "report_type": "3",
        }
        r = requests.get("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py",
                         params=params, headers=UA, timeout=300)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text), na_values=["M"])
        if not df.empty and "valid" in df:
            break
    else:
        return []
    df["valid"] = pd.to_datetime(df["valid"], utc=True)
    f2c = lambda f: (f - 32) * 5 / 9  # noqa: E731
    out = pd.DataFrame({
        "valid": df["valid"],
        "t2m": f2c(pd.to_numeric(df["tmpf"], errors="coerce")),
        "d2m": f2c(pd.to_numeric(df["dwpf"], errors="coerce")),
        "wspd": pd.to_numeric(df["sknt"], errors="coerce") * KT_TO_MS,
        "gust": pd.to_numeric(df["gust"], errors="coerce") * KT_TO_MS,
        "mslp": pd.to_numeric(df["mslp"], errors="coerce"),
    })
    out = out.astype(object).where(out.notna(), None)
    return out.to_dict("records")


def recent_history(station: str, hours: int = 24) -> list[dict]:
    end = datetime.now(timezone.utc)
    return history(station, end - timedelta(hours=hours), end + timedelta(days=1))
