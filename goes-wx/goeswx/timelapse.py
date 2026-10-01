"""Turn rendered frames into MP4 loops."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import imageio.v2 as imageio
import numpy as np

from .config import Config
from .imagery import FRAME_FMT, frame_dir

log = logging.getLogger(__name__)


def frames_since(cfg: Config, product: str, hours: float) -> list[Path]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out = []
    for p in sorted(frame_dir(cfg, product).glob("*.png")):
        try:
            t = datetime.strptime(p.stem, FRAME_FMT).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if t >= cutoff:
            out.append(p)
    return out


def build(cfg: Config, hours: float | None = None) -> list[Path]:
    hours = hours or cfg["timelapse"]["hours"]
    fps = cfg["timelapse"]["fps"]
    out_dir = cfg.output_dir / "timelapse"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for product in cfg["imagery"]["products"]:
        frames = frames_since(cfg, product, hours)
        if len(frames) < 2:
            log.info("%s: only %d frame(s), skipping timelapse", product, len(frames))
            continue
        out = out_dir / f"{product}.mp4"
        tmp = out.with_name(out.stem + ".tmp.mp4")
        with imageio.get_writer(tmp, fps=fps, codec="libx264", quality=8, pixelformat="yuv420p",
                                macro_block_size=2, ffmpeg_log_level="error") as w:
            for f in frames:
                w.append_data(np.asarray(imageio.imread(f))[..., :3])
            for _ in range(fps):           # hold the latest frame for a second before looping
                w.append_data(np.asarray(imageio.imread(frames[-1]))[..., :3])
        tmp.replace(out)
        written.append(out)
        log.info("%s: %d frames -> %s", product, len(frames), out)
    return written
