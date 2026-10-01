"""Current conditions: station observation + what GOES sees overhead + lightning."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from . import obs, store
from .config import Config

log = logging.getLogger(__name__)


def update(cfg: Config) -> dict:
    loc = cfg["location"]
    result = {"updated": datetime.now(timezone.utc).isoformat(), "station": None, "satellite": None}
    try:
        m = obs.latest_metar(loc["metar_station"])
        if m and m["valid"]:
            store.save_obs(cfg.data_dir / "goeswx.db", [m], "metar")
            m["valid"] = m["valid"].isoformat()
        result["station"] = m
    except Exception as e:
        log.warning("METAR fetch failed: %s", e)
        result["station_error"] = str(e)

    sat_path = cfg.data_dir / "satellite_point.json"
    if sat_path.exists():
        result["satellite"] = json.loads(sat_path.read_text())
    (cfg.output_dir / "current.json").write_text(json.dumps(result, indent=1, default=str))
    return result
