"""Publish the static dashboard: index.html plus a small site.json the page reads."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from importlib import resources

from .config import Config
from .imagery import FRAME_FMT, frame_dir


def build(cfg: Config) -> None:
    out = cfg.output_dir
    latest = {}
    for product in cfg["imagery"]["products"]:
        frames = sorted(frame_dir(cfg, product).glob("*.png"))
        if frames:
            t = datetime.strptime(frames[-1].stem, FRAME_FMT).replace(tzinfo=timezone.utc)
            latest[product] = {"file": frames[-1].name, "time": t.isoformat()}
    site = {
        "name": cfg["location"]["name"],
        "units": cfg["units"],
        "products": cfg["imagery"]["products"],
        "timelapse_hours": cfg["timelapse"]["hours"],
        "lightning_radius_km": cfg["location"]["lightning_radius_km"],
        "latest": latest,
        "generated": datetime.now(timezone.utc).isoformat(),
    }
    (out / "site.json").write_text(json.dumps(site, indent=1))
    report = cfg.data_dir / "mos_report.json"
    if report.exists():
        shutil.copy(report, out / "mos_report.json")
    html = resources.files("goeswx").joinpath("web/index.html").read_text()
    (out / "index.html").write_text(html)
