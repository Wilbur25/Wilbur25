"""GOES Geostationary Lightning Mapper (GLM) flashes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import netCDF4
import numpy as np

from . import aws

PRODUCT = "GLM-L2-LCFA"   # one file every 20 seconds


def _read(content: bytes) -> tuple[np.ndarray, np.ndarray]:
    with netCDF4.Dataset("glm.nc", memory=content) as ds:
        if "flash_lat" not in ds.variables or ds.dimensions["number_of_flashes"].size == 0:
            return np.empty(0), np.empty(0)
        return (np.asarray(ds["flash_lat"][:], dtype=float),
                np.asarray(ds["flash_lon"][:], dtype=float))


def flashes(bucket: str, end: datetime, minutes: int = 10, region=None) -> tuple[np.ndarray, np.ndarray]:
    """All flash (lat, lon) in the `minutes` before `end`, optionally cropped to region."""
    files = aws.goes_files_between(bucket, PRODUCT, end - timedelta(minutes=minutes), end)
    with ThreadPoolExecutor(8) as pool:   # download in parallel...
        blobs = list(pool.map(lambda f: aws.get_bytes(bucket, f[1]), files))
    parts = [_read(b) for b in blobs]     # ...but decode serially: HDF5 is not thread-safe
    lat = np.concatenate([p[0] for p in parts]) if parts else np.empty(0)
    lon = np.concatenate([p[1] for p in parts]) if parts else np.empty(0)
    if region is not None and len(lat):
        lon_min, lon_max, lat_min, lat_max = region
        keep = (lon >= lon_min) & (lon <= lon_max) & (lat >= lat_min) & (lat <= lat_max)
        lat, lon = lat[keep], lon[keep]
    return lat, lon


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def summary(lat: np.ndarray, lon: np.ndarray, home_lat: float, home_lon: float, radius_km: float) -> dict:
    """Counts and nearest-strike distance relative to a location."""
    if not len(lat):
        return {"flashes_region": 0, "flashes_nearby": 0, "nearest_km": None}
    d = haversine_km(home_lat, home_lon, lat, lon)
    return {
        "flashes_region": int(len(lat)),
        "flashes_nearby": int((d <= radius_km).sum()),
        "nearest_km": round(float(d.min()), 1),
    }
