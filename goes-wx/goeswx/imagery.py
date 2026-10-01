"""Fetch new ABI scans from AWS, render frames and record satellite readings at home."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import abi, aws, glm, render
from .config import Config

log = logging.getLogger(__name__)

FRAME_FMT = "%Y%m%dT%H%M"


def frame_dir(cfg: Config, product: str) -> Path:
    d = cfg.output_dir / "frames" / product
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pick_scans(files, interval_min: int, done: set[str]):
    """Newest-first thinning: keep scans at least interval_min apart, skip ones already rendered."""
    picked, last = [], None
    for t, key, size in sorted(files, reverse=True):
        if last is None or (last - t) >= timedelta(minutes=interval_min) - timedelta(seconds=30):
            last = t
            if t.strftime(FRAME_FMT) not in done:
                picked.append((t, key, size))
    return sorted(picked)


def satellite_point(scene: abi.Scene, lat: float, lon: float) -> dict:
    """What the satellite sees directly over a location."""
    ir = scene.value_at("C13", lat, lon) - 273.15
    vis = scene.value_at("C02", lat, lon)
    sza = float(abi.solar_zenith(lat, lon, scene.time))
    # Very rough cloud test: cold IR, or bright visible in daylight.
    cloudy = bool(ir < -5 or (sza < 75 and vis > 0.3))
    return {
        "time": scene.time.isoformat(),
        "cloud_top_temp_c": round(ir, 1),
        "visible_reflectance": None if sza >= 85 else round(vis, 3),
        "solar_zenith_deg": round(sza, 1),
        "cloudy": cloudy,
        "deep_convection": bool(ir < -50),
    }


def update(cfg: Config, hours: float | None = None) -> list[Path]:
    """Render any scans from the last `hours` (default: one interval) not yet rendered."""
    sat, img, loc = cfg["satellite"], cfg["imagery"], cfg["location"]
    now = datetime.now(timezone.utc)
    lookback = timedelta(hours=hours) if hours else timedelta(minutes=sat["interval_minutes"] + 15)
    files = aws.goes_files_between(sat["bucket"], sat["product"], now - lookback, now)
    if not files:
        log.warning("no %s files found in the last %s", sat["product"], lookback)
        return []

    done = {p.stem for p in frame_dir(cfg, img["products"][-1]).glob("*.png")}
    scans = _pick_scans(files, sat["interval_minutes"], done)
    raw_dir = cfg.data_dir / "raw" / sat["product"]
    written = []
    for i, (t, key, size) in enumerate(scans):
        log.info("scan %s (%.0f MB)", t.strftime("%Y-%m-%d %H:%M"), size / 1e6)
        path = aws.download(sat["bucket"], key, raw_dir / Path(key).name)
        try:
            scene = abi.load_mcmip(path, cfg["region"])
        finally:
            if not sat["keep_raw"]:
                path.unlink(missing_ok=True)

        lightning = None
        if img["lightning_overlay"]:
            try:
                lightning = glm.flashes(sat["bucket"], t + timedelta(minutes=5), 10, cfg["region"])
            except Exception as e:  # lightning is a nice-to-have; never lose a frame over it
                log.warning("GLM fetch failed: %s", e)

        stem = t.strftime(FRAME_FMT)
        for product in img["products"]:
            out = frame_dir(cfg, product) / f"{stem}.png"
            render.render(scene, product, out, width_px=img["width_px"], dpi=img["dpi"],
                          marker=(loc["lat"], loc["lon"], loc["name"].split(",")[0]),
                          lightning=lightning, tz=cfg.tz)
            written.append(out)

        if i == len(scans) - 1:
            point = satellite_point(scene, loc["lat"], loc["lon"])
            if lightning is not None:
                point["lightning"] = glm.summary(*lightning, loc["lat"], loc["lon"],
                                                 loc["lightning_radius_km"])
            (cfg.data_dir / "satellite_point.json").write_text(json.dumps(point, indent=2))
    prune_frames(cfg)
    return written


def prune_frames(cfg: Config) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=cfg["timelapse"]["keep_frames_hours"])
    for product in cfg["imagery"]["products"]:
        for p in frame_dir(cfg, product).glob("*.png"):
            try:
                t = datetime.strptime(p.stem, FRAME_FMT).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if t < cutoff:
                p.unlink()
