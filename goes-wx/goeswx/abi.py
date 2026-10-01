"""Read GOES-R ABI multiband (MCMIP) files and build image composites."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pyproj
import xarray as xr

from .solar import solar_zenith


@dataclass
class Scene:
    """A cropped ABI scene on the native geostationary grid."""

    time: datetime
    bands: dict[str, np.ndarray]   # "C01".."C16": reflectance (0-1) or brightness temperature (K)
    x: np.ndarray                  # projection x coordinates in metres (1-D)
    y: np.ndarray                  # projection y coordinates in metres (1-D, decreasing)
    lat: np.ndarray                # 2-D, NaN off the Earth's disk
    lon: np.ndarray
    proj: dict                     # goes_imager_projection attributes
    satellite: str

    @property
    def extent(self) -> tuple[float, float, float, float]:
        """Pixel-edge extent (left, right, bottom, top) in projection metres for imshow."""
        dx = abs(self.x[1] - self.x[0]) / 2
        dy = abs(self.y[1] - self.y[0]) / 2
        return (self.x[0] - dx, self.x[-1] + dx, self.y[-1] - dy, self.y[0] + dy)

    def value_at(self, band: str, lat: float, lon: float) -> float:
        """Nearest-pixel value of a band at a location (NaN if outside the scene)."""
        d2 = (self.lat - lat) ** 2 + ((self.lon - lon) * np.cos(np.radians(lat))) ** 2
        if not np.isfinite(d2).any():
            return float("nan")
        i, j = np.unravel_index(np.nanargmin(d2), d2.shape)
        if d2[i, j] > 0.1**2:  # more than ~10 km from the nearest pixel: outside the scene
            return float("nan")
        return float(self.bands[band][i, j])


def geos_crs(proj: dict) -> pyproj.CRS:
    return pyproj.CRS.from_dict({
        "proj": "geos",
        "h": float(proj["perspective_point_height"]),
        "lon_0": float(proj["longitude_of_projection_origin"]),
        "sweep": str(proj["sweep_angle_axis"]),
        "a": float(proj["semi_major_axis"]),
        "b": float(proj["semi_minor_axis"]),
    })


def _bbox_index(xm: np.ndarray, ym: np.ndarray, crs: pyproj.CRS, region) -> tuple[slice, slice]:
    """Row/column slices of the grid covering a lon/lat bounding box."""
    lon_min, lon_max, lat_min, lat_max = region
    t = np.linspace(0, 1, 50)
    lons = np.concatenate([lon_min + (lon_max - lon_min) * t, np.full(50, lon_max),
                           lon_max - (lon_max - lon_min) * t, np.full(50, lon_min)])
    lats = np.concatenate([np.full(50, lat_min), lat_min + (lat_max - lat_min) * t,
                           np.full(50, lat_max), lat_max - (lat_max - lat_min) * t])
    fwd = pyproj.Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    px, py = fwd.transform(lons, lats)
    ok = np.isfinite(px) & np.isfinite(py) & (np.abs(px) < 1e8)
    if not ok.any():
        raise ValueError("region is not visible from this satellite")
    px, py = px[ok], py[ok]
    cols = np.where((xm >= px.min()) & (xm <= px.max()))[0]
    rows = np.where((ym >= py.min()) & (ym <= py.max()))[0]
    if len(cols) < 2 or len(rows) < 2:
        raise ValueError("region is outside this file's coverage (try ABI-L2-MCMIPF)")
    return slice(rows[0], rows[-1] + 1), slice(cols[0], cols[-1] + 1)


def load_mcmip(path, region, bands=None) -> Scene:
    """Load an ABI-L2-MCMIP file, cropped to region = [lon_min, lon_max, lat_min, lat_max]."""
    bands = bands or [f"C{i:02d}" for i in range(1, 17)]
    with xr.open_dataset(path, engine="netcdf4") as ds:
        proj = {k: v for k, v in ds["goes_imager_projection"].attrs.items()}
        crs = geos_crs(proj)
        h = float(proj["perspective_point_height"])
        xm = ds["x"].values.astype("float64") * h
        ym = ds["y"].values.astype("float64") * h
        rows, cols = _bbox_index(xm, ym, crs, region)
        data = {b: ds[f"CMI_{b}"][rows, cols].values.astype("float32") for b in bands}
        start = ds.attrs["time_coverage_start"].rstrip("Z")
        sat = ds.attrs.get("platform_ID", "GOES")
    xs, ys = xm[cols], ym[rows]
    xx, yy = np.meshgrid(xs, ys)
    inv = pyproj.Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    lon, lat = inv.transform(xx, yy)
    lon = np.where(np.abs(lon) > 360, np.nan, lon)
    lat = np.where(np.abs(lat) > 90, np.nan, lat)
    t = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    return Scene(t, data, xs, ys, lat, lon, proj, sat)


def _norm(a, lo, hi):
    return np.clip((a - lo) / (hi - lo), 0, 1)


def true_color(s: Scene) -> np.ndarray:
    """CIMSS-style natural true colour by day, blended into clean-IR clouds at night."""
    r, b, nir = s.bands["C02"], s.bands["C01"], s.bands["C03"]
    g = 0.45 * r + 0.1 * nir + 0.45 * b                  # ABI has no green band
    rgb = np.stack([r, g, b], axis=-1)
    rgb = np.clip(np.nan_to_num(rgb), 0, 1) ** (1 / 2.2)
    # Night side: cold (high) clouds bright, warm surface dark, on a dark blue background.
    ir = _norm(np.nan_to_num(s.bands["C13"], nan=300.0), 290, 200)[..., None]
    night = np.clip(np.array([0.02, 0.04, 0.10]) + ir * np.array([0.95, 0.95, 0.92]), 0, 1)
    sza = solar_zenith(np.nan_to_num(s.lat), np.nan_to_num(s.lon), s.time)
    w = _norm(sza, 80, 95)[..., None]                    # 0 = full day, 1 = full night
    out = rgb * (1 - w) + night * w
    out[~np.isfinite(s.lat)] = 0
    return out.astype("float32")


def airmass(s: Scene) -> np.ndarray:
    """EUMETSAT airmass RGB: jet streaks, dry intrusions and air mass boundaries."""
    r = _norm(s.bands["C08"] - s.bands["C10"], -26.2, 0.6)
    g = _norm(s.bands["C12"] - s.bands["C13"], -43.2, 6.7)
    b = _norm(s.bands["C08"], 243.9, 208.5)
    out = np.nan_to_num(np.stack([r, g, b], axis=-1))
    return out.astype("float32")


def brightness_c(s: Scene, band: str) -> np.ndarray:
    """Brightness temperature in degrees Celsius."""
    return s.bands[band] - 273.15
