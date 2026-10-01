"""Daily forecast maps (high temperature, precipitation) for the region.

Runs in its own process because cartopy and eccodes can't share one (see gfs.py).
"""

from __future__ import annotations

import json
import logging

import matplotlib

matplotlib.use("Agg")
import cartopy.crs as ccrs  # noqa: E402
import cartopy.feature as cfeature  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import BoundaryNorm, ListedColormap  # noqa: E402

from .config import Config  # noqa: E402

log = logging.getLogger(__name__)

PRECIP_IN = [0.01, 0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4, 6]
PRECIP_MM = [0.25, 2.5, 5, 10, 20, 25, 40, 50, 75, 100, 150]
PRECIP_COLORS = ["#c6e9ff", "#8fd3ff", "#3fa9f5", "#1f6fd1", "#1aa64b", "#7ad151",
                 "#f5e027", "#f59b27", "#e8402a", "#b3126e", "#7a2ba8"]


def render(cfg: Config) -> list[str]:
    path = cfg.data_dir / "forecast_grids.npz"
    if not path.exists():
        return []
    z = np.load(path)
    lats, lons, days = z["lats"], z["lons"], [str(d) for d in z["days"]]
    imp = cfg.imperial
    lon_min, lon_max, lat_min, lat_max = cfg["region"]
    proj = ccrs.LambertConformal(central_longitude=(lon_min + lon_max) / 2,
                                 central_latitude=(lat_min + lat_max) / 2)
    states = cfeature.NaturalEarthFeature("cultural", "admin_1_states_provinces_lakes", "50m",
                                          facecolor="none")
    loc = cfg["location"]
    out_dir = cfg.output_dir / "maps"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []

    for i, day in enumerate(days):
        label = pd.Timestamp(day).strftime("%a %b %-d")
        for kind in ("tmax", "precip"):
            fig = plt.figure(figsize=(8, 5.6), dpi=100)
            fig.patch.set_facecolor("#0f1720")
            ax = fig.add_axes([0.02, 0.1, 0.96, 0.82], projection=proj)
            ax.set_extent(cfg["region"], crs=ccrs.PlateCarree())
            if kind == "tmax":
                v = z["tmax"][i] * 9 / 5 + 32 if imp else z["tmax"][i]
                step = 5 if imp else 2.5
                levels = np.arange(np.floor(np.nanmin(v) / step) * step, np.nanmax(v) + step, step)
                cf = ax.contourf(lons, lats, v, levels=levels, cmap="turbo",
                                 transform=ccrs.PlateCarree(), extend="both")
                cs = ax.contour(lons, lats, v, levels=levels[::2], colors="k", linewidths=0.4,
                                transform=ccrs.PlateCarree())
                ax.clabel(cs, fontsize=7, fmt="%d")
                title, unit = "High temperature", "°F" if imp else "°C"
            else:
                v = z["precip"][i] / 25.4 if imp else z["precip"][i]
                bounds = PRECIP_IN if imp else PRECIP_MM
                cmap = ListedColormap(PRECIP_COLORS)
                cmap.set_under((0, 0, 0, 0))
                cf = ax.contourf(lons, lats, v, levels=bounds + [bounds[-1] * 10], cmap=cmap,
                                 norm=BoundaryNorm(bounds + [bounds[-1] * 10], cmap.N),
                                 transform=ccrs.PlateCarree(), extend="min")
                title, unit = "Total precipitation", "in" if imp else "mm"
                ax.set_facecolor("#e9e5dc")
            ax.add_feature(states, edgecolor="#333", linewidth=0.4)
            ax.add_feature(cfeature.BORDERS.with_scale("50m"), edgecolor="#222", linewidth=0.7)
            ax.add_feature(cfeature.COASTLINE.with_scale("50m"), edgecolor="#222", linewidth=0.7)
            ax.plot(loc["lon"], loc["lat"], "o", ms=5, mfc="#ff3b30", mec="white",
                    transform=ccrs.PlateCarree())
            cax = fig.add_axes([0.1, 0.035, 0.8, 0.025])
            cb = fig.colorbar(cf, cax=cax, orientation="horizontal")
            cb.ax.tick_params(colors="white", labelsize=8)
            cb.set_label(unit, color="white", fontsize=8)
            cb.ax.xaxis.set_label_position("top")
            fig.text(0.02, 0.96, f"{title} — {label}", color="white", fontsize=12, va="center")
            fig.text(0.98, 0.96, f"GFS {str(z['run'])[:13].replace('T', ' ')}Z", color="#aaa",
                     fontsize=8, va="center", ha="right")
            out = out_dir / f"{kind}_{i}.png"
            fig.savefig(out, facecolor=fig.get_facecolor())
            plt.close(fig)
            written.append(out.name)
    (out_dir / "index.json").write_text(json.dumps({"days": days, "count": len(days)}))
    for old in out_dir.glob("*_*.png"):     # remove maps from longer earlier runs
        if old.name not in written:
            old.unlink()
    return written
