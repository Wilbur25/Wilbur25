"""Pull just the fields we need from NOAA GFS GRIB2 files on AWS, using byte ranges.

GFS files are ~500 MB each; the .idx sidecar lists where each field starts, so we
download only the ~1 MB fields we use. Must not import cartopy (eccodes' bundled
libraries clash with it in one process), so map rendering lives in forecast_maps.py.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

from . import aws

log = logging.getLogger(__name__)

BUCKET = "noaa-gfs-bdp-pds"

# name: (GRIB short name, level text in the .idx, kind)
#   inst = instantaneous value at the step, acc0 = accumulated since the run started,
#   max/min = extreme over the window ending at the step.
FIELDS = {
    "t2m": ("TMP", "2 m above ground", "inst"),        # K
    "d2m": ("DPT", "2 m above ground", "inst"),        # K
    "rh": ("RH", "2 m above ground", "inst"),          # %
    "tmax": ("TMAX", "2 m above ground", "max"),       # K
    "tmin": ("TMIN", "2 m above ground", "min"),       # K
    "u10": ("UGRD", "10 m above ground", "inst"),      # m/s
    "v10": ("VGRD", "10 m above ground", "inst"),      # m/s
    "gust": ("GUST", "surface", "inst"),               # m/s
    "mslp": ("PRMSL", "mean sea level", "inst"),       # Pa
    "tcc": ("TCDC", "entire atmosphere", "inst"),      # %
    "apcp": ("APCP", "surface", "acc0"),               # kg/m2 == mm
    "crain": ("CRAIN", "surface", "inst"),             # 0/1
    "csnow": ("CSNOW", "surface", "inst"),             # 0/1
    "cape": ("CAPE", "surface", "inst"),               # J/kg
}

MOS_FIELDS = ["t2m", "d2m", "u10", "v10", "gust", "mslp", "tcc", "apcp"]
MAP_FIELDS = ["t2m", "tmax", "tmin", "apcp", "tcc"]

_WINDOW = re.compile(r"^(\d+)-(\d+) (hour|day) (acc|max|min|ave) fcst$")
_INST = re.compile(r"^(\d+) (hour|day) fcst$")


@dataclass
class IdxEntry:
    var: str
    level: str
    kind: str          # inst, acc, max, min, ave
    start_h: int
    end_h: int
    offset: int
    end: int | None    # inclusive byte end; None = to end of file


def parse_time_range(text: str) -> tuple[str, int, int] | None:
    """'anl' / '24 hour fcst' / '18-24 hour max fcst' / '0-1 day acc fcst' -> (kind, start_h, end_h)."""
    if text == "anl":
        return ("inst", 0, 0)
    m = _INST.match(text)
    if m:
        h = int(m.group(1)) * (24 if m.group(2) == "day" else 1)
        return ("inst", h, h)
    m = _WINDOW.match(text)
    if m:
        mult = 24 if m.group(3) == "day" else 1
        return (m.group(4), int(m.group(1)) * mult, int(m.group(2)) * mult)
    return None


def parse_idx(text: str) -> list[IdxEntry]:
    rows = [line.split(":") for line in text.strip().splitlines() if line]
    out = []
    for i, r in enumerate(rows):
        tr = parse_time_range(r[5])
        if tr is None:
            continue
        end = int(rows[i + 1][1]) - 1 if i + 1 < len(rows) else None
        out.append(IdxEntry(r[3], r[4], tr[0], tr[1], tr[2], int(r[1]), end))
    return out


def select(entries: list[IdxEntry], step: int, names) -> dict[str, IdxEntry]:
    """Pick the idx entry for each wanted field at a forecast step."""
    out = {}
    for name in names:
        var, level, kind = FIELDS[name]
        for e in entries:
            if e.var != var or e.level != level or e.end_h != step:
                continue
            if kind == "inst" and e.kind == "inst":
                out[name] = e
            elif kind == "acc0" and e.kind == "acc" and e.start_h == 0:
                out[name] = e
            elif kind in ("max", "min") and e.kind == kind:
                out[name] = e
            if name in out:
                break
    return out


def file_key(run: datetime, step: int, res: str) -> str:
    return f"gfs.{run:%Y%m%d}/{run:%H}/atmos/gfs.t{run:%H}z.pgrb2.{res}.f{step:03d}"


def steps_for(days: int, step_hours: int) -> list[int]:
    """Forecast hours to fetch. Hourly output only exists to f120; beyond that it is 3-hourly."""
    last = days * 24
    out, h = [], 0
    while h <= last:
        out.append(h)
        h += step_hours if (h < 120 or step_hours >= 3) else 3
    return out


def latest_run(res: str, last_step: int, now: datetime | None = None) -> datetime:
    """Newest GFS cycle whose final needed file has been published."""
    now = now or datetime.now(timezone.utc)
    run = now.replace(hour=now.hour - now.hour % 6, minute=0, second=0, microsecond=0)
    for _ in range(8):
        try:
            aws.get_range(BUCKET, file_key(run, last_step, res) + ".idx", 0, 0)
            return run
        except Exception:
            run -= timedelta(hours=6)
    raise RuntimeError("no complete GFS run found in the last 48 hours")


# --- GRIB decoding ------------------------------------------------------------

@dataclass
class Grid:
    values: np.ndarray   # (nlat, nlon), lat descending, lon ascending from lon0
    lat0: float
    lon0: float
    dlat: float          # positive spacing
    dlon: float

    def point(self, lat: float, lon: float) -> float:
        """Bilinear interpolation at a location."""
        ny, nx = self.values.shape
        fy = (self.lat0 - lat) / self.dlat
        fx = ((lon - self.lon0) % 360) / self.dlon
        y0, x0 = int(np.floor(fy)), int(np.floor(fx))
        wy, wx = fy - y0, fx - x0
        y1, x1 = min(y0 + 1, ny - 1), (x0 + 1) % nx
        v = self.values
        return float((1 - wy) * ((1 - wx) * v[y0, x0] + wx * v[y0, x1])
                     + wy * ((1 - wx) * v[y1, x0] + wx * v[y1, x1]))

    def crop(self, region) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(lats, lons, values) covering region = [lon_min, lon_max, lat_min, lat_max]."""
        lon_min, lon_max, lat_min, lat_max = region
        ny, nx = self.values.shape
        lats = self.lat0 - self.dlat * np.arange(ny)
        lons = self.lon0 + self.dlon * np.arange(nx)
        lons = ((lons + 180) % 360) - 180
        pad = 4  # degrees; a Lambert map of the region bulges past its lon/lat box
        rows = np.where((lats >= lat_min - pad) & (lats <= lat_max + pad))[0]
        cols = np.where((lons >= lon_min - pad) & (lons <= lon_max + pad))[0]
        order = cols[np.argsort(lons[cols])]
        return lats[rows], lons[order], self.values[np.ix_(rows, order)]


def decode(message: bytes) -> Grid:
    import eccodes  # imported lazily: see module docstring

    gid = eccodes.codes_new_from_message(message)
    try:
        ni = eccodes.codes_get(gid, "Ni")
        nj = eccodes.codes_get(gid, "Nj")
        vals = eccodes.codes_get_values(gid).reshape(nj, ni)
        lat0 = eccodes.codes_get(gid, "latitudeOfFirstGridPointInDegrees")
        lat1 = eccodes.codes_get(gid, "latitudeOfLastGridPointInDegrees")
        lon0 = eccodes.codes_get(gid, "longitudeOfFirstGridPointInDegrees")
        dlat = eccodes.codes_get(gid, "jDirectionIncrementInDegrees")
        dlon = eccodes.codes_get(gid, "iDirectionIncrementInDegrees")
        missing = eccodes.codes_get(gid, "missingValue")
    finally:
        eccodes.codes_release(gid)
    vals = np.where(vals == missing, np.nan, vals)
    if lat0 < lat1:  # make latitude descending
        vals, lat0 = vals[::-1], lat1
    return Grid(vals, lat0, lon0, dlat, dlon)


def fetch_step(run: datetime, step: int, res: str, names, lat: float, lon: float,
               region=None, region_names=()) -> dict:
    """Point values (and optional regional grids) for one forecast step."""
    key = file_key(run, step, res)
    entries = parse_idx(aws.get_text(BUCKET, key + ".idx"))
    chosen = select(entries, step, names)
    point, grids = {}, {}
    for name, e in chosen.items():
        g = decode(aws.get_range(BUCKET, key, e.offset, e.end))
        point[name] = g.point(lat, lon)
        if region is not None and name in region_names:
            grids[name] = g.crop(region)
    return {"step": step, "valid": run + timedelta(hours=step), "point": point, "grids": grids}


def fetch_run(run: datetime, steps, res: str, names, lat: float, lon: float,
              region=None, region_names=(), workers: int = 8) -> list[dict]:
    def one(step):
        for attempt in range(3):
            try:
                return fetch_step(run, step, res, names, lat, lon, region, region_names)
            except Exception as e:
                if attempt == 2:
                    log.warning("GFS f%03d failed: %s", step, e)
                    return None

    with ThreadPoolExecutor(workers) as pool:
        results = [r for r in pool.map(one, steps) if r is not None]
    return sorted(results, key=lambda r: r["step"])
