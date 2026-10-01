"""Configuration loading with defaults."""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

DEFAULTS = {
    "location": {
        "name": "Kansas City, MO",
        "lat": 39.0997,
        "lon": -94.5786,
        "metar_station": "KMCI",
        "lightning_radius_km": 50,
    },
    "region": [-104.0, -85.0, 32.0, 45.0],
    "units": "imperial",
    "timezone": None,
    "satellite": {
        "bucket": "noaa-goes19",
        "product": "ABI-L2-MCMIPC",
        "interval_minutes": 10,
        "keep_raw": False,
    },
    "imagery": {
        "products": ["truecolor", "ir", "watervapor", "airmass"],
        "lightning_overlay": True,
        "dpi": 100,
        "width_px": 1280,
    },
    "timelapse": {"hours": 6, "fps": 12, "keep_frames_hours": 72},
    "forecast": {"days": 7, "step_hours": 3, "resolution": "0p25", "maps": True},
    "paths": {"data": "./data", "output": "./output"},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


@dataclass
class Config:
    raw: dict
    root: Path

    def __getitem__(self, key):
        return self.raw[key]

    @property
    def data_dir(self) -> Path:
        return self._path("data")

    @property
    def output_dir(self) -> Path:
        return self._path("output")

    def _path(self, key: str) -> Path:
        p = Path(self.raw["paths"][key]).expanduser()
        if not p.is_absolute():
            p = self.root / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def tz(self):
        """Local time zone for daily forecasts: config value, else the system's."""
        if self.raw.get("timezone"):
            from zoneinfo import ZoneInfo
            return ZoneInfo(self.raw["timezone"])
        from zoneinfo import ZoneInfo
        try:  # /etc/localtime -> /usr/share/zoneinfo/America/Chicago
            return ZoneInfo(str(Path("/etc/localtime").resolve()).split("zoneinfo/", 1)[1])
        except Exception:
            return datetime.now().astimezone().tzinfo

    @property
    def imperial(self) -> bool:
        return self.raw["units"] == "imperial"


def load(path: str | os.PathLike | None = None) -> Config:
    """Load config.yaml (or $GOESWX_CONFIG) merged over the defaults."""
    path = path or os.environ.get("GOESWX_CONFIG") or "config.yaml"
    path = Path(path).expanduser().resolve()
    user = {}
    if path.exists():
        with open(path) as f:
            user = yaml.safe_load(f) or {}
    raw = _merge(DEFAULTS, user)
    lon_min, lon_max, lat_min, lat_max = raw["region"]
    if not (lon_min < lon_max and lat_min < lat_max):
        raise ValueError("region must be [lon_min, lon_max, lat_min, lat_max]")
    if raw["forecast"]["step_hours"] not in (1, 3, 6):
        raise ValueError("forecast.step_hours must be 1, 3 or 6")
    return Config(raw=raw, root=path.parent)
