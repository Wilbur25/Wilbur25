"""Draw satellite scenes onto maps."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import cartopy.crs as ccrs  # noqa: E402
import cartopy.feature as cfeature  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, Normalize  # noqa: E402

from . import abi  # noqa: E402

PRODUCTS = {
    "truecolor": "True Color",
    "ir": "Clean Infrared (10.3 µm)",
    "watervapor": "Mid-level Water Vapor (6.9 µm)",
    "airmass": "Airmass RGB",
}


def _cmap(name, stops, vmin, vmax):
    pos = [(t - vmin) / (vmax - vmin) for t, _ in stops]
    return LinearSegmentedColormap.from_list(name, list(zip(pos, [c for _, c in stops])))


# Enhanced IR: greys for warm surfaces and low cloud, colours for cold storm tops.
IR_RANGE = (-90.0, 50.0)
IR_CMAP = _cmap("ir_enhanced", [
    (-90, "#9b30ff"), (-80, "#ffffff"), (-72, "#600000"), (-62, "#ff0000"), (-52, "#ffff00"),
    (-42, "#00ff60"), (-31, "#00a0ff"), (-30, "#d0d0d0"), (50, "#000000"),
], *IR_RANGE)

WV_RANGE = (-75.0, 5.0)
WV_CMAP = _cmap("wv", [
    (-75, "#ffffff"), (-58, "#1c9c3a"), (-45, "#3a7bd5"), (-30, "#e6e6e6"),
    (-18, "#b35900"), (5, "#3d1f00"),
], *WV_RANGE)


def _geos(scene: abi.Scene) -> ccrs.Geostationary:
    p = scene.proj
    globe = ccrs.Globe(semimajor_axis=float(p["semi_major_axis"]),
                       semiminor_axis=float(p["semi_minor_axis"]), ellipse=None)
    return ccrs.Geostationary(central_longitude=float(p["longitude_of_projection_origin"]),
                              satellite_height=float(p["perspective_point_height"]),
                              sweep_axis=str(p["sweep_angle_axis"]), globe=globe)


_STATES = cfeature.NaturalEarthFeature("cultural", "admin_1_states_provinces_lakes", "50m",
                                       facecolor="none")


def render(scene: abi.Scene, product: str, out: Path, *, width_px=1280, dpi=100,
           marker=None, lightning=None, tz=None) -> Path:
    """Render one product to a PNG. marker=(lat, lon, label); lightning=(lats, lons)."""
    crs = _geos(scene)
    left, right, bottom, top = scene.extent
    width_px -= width_px % 2
    height_px = int(round(width_px * (top - bottom) / (right - left)))
    height_px -= height_px % 2                       # even sizes keep H.264 encoders happy

    fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1], projection=crs)
    ax.set_extent(scene.extent, crs=crs)
    ax.set_facecolor("black")

    cbar = None
    if product == "truecolor":
        img = abi.true_color(scene)
        ax.imshow(img, extent=scene.extent, transform=crs, origin="upper", interpolation="nearest")
        edge = "#f2d16b"
    elif product == "airmass":
        ax.imshow(abi.airmass(scene), extent=scene.extent, transform=crs, origin="upper",
                  interpolation="nearest")
        edge = "white"
    elif product in ("ir", "watervapor"):
        band, cmap, rng = ("C13", IR_CMAP, IR_RANGE) if product == "ir" else ("C09", WV_CMAP, WV_RANGE)
        im = ax.imshow(abi.brightness_c(scene, band), extent=scene.extent, transform=crs,
                       origin="upper", cmap=cmap, norm=Normalize(*rng), interpolation="nearest")
        cbar = im
        edge = "white" if product == "ir" else "black"
    else:
        raise ValueError(f"unknown product {product!r}; choose from {', '.join(PRODUCTS)}")

    ax.add_feature(_STATES, edgecolor=edge, linewidth=0.4, alpha=0.6)
    ax.add_feature(cfeature.BORDERS.with_scale("50m"), edgecolor=edge, linewidth=0.7, alpha=0.8)
    ax.add_feature(cfeature.COASTLINE.with_scale("50m"), edgecolor=edge, linewidth=0.7, alpha=0.8)

    if lightning is not None and product in ("ir", "truecolor") and len(lightning[0]):
        ax.scatter(lightning[1], lightning[0], s=10, marker="+", c="#ffee00", linewidths=0.8,
                   transform=ccrs.PlateCarree(), zorder=5)
    if marker:
        lat, lon, label = marker
        ax.plot(lon, lat, marker="o", ms=5, mfc="#ff3b30", mec="white", mew=1,
                transform=ccrs.PlateCarree(), zorder=6)
        ax.text(lon, lat, f"  {label}", color="white", fontsize=8, va="center",
                transform=ccrs.PlateCarree(), zorder=6,
                bbox=dict(boxstyle="round,pad=0.15", fc="black", alpha=0.45, lw=0))

    local = scene.time.astimezone(tz)
    title = (f"{scene.satellite}  {PRODUCTS[product]}   {scene.time:%Y-%m-%d %H:%M} UTC"
             f"  ({local:%a %-I:%M %p %Z})")
    fig.text(0.008, 0.99, title, color="white", fontsize=10, va="top", ha="left",
             bbox=dict(boxstyle="round,pad=0.3", fc="black", alpha=0.6, lw=0))
    if lightning is not None and product in ("ir", "truecolor"):
        fig.text(0.008, 0.945, f"+ GLM lightning, last 10 min ({len(lightning[0])} flashes)",
                 color="#ffee00", fontsize=8, va="top",
                 bbox=dict(boxstyle="round,pad=0.25", fc="black", alpha=0.5, lw=0))
    if cbar is not None:
        cax = fig.add_axes([0.62, 0.03, 0.36, 0.022])
        cb = fig.colorbar(cbar, cax=cax, orientation="horizontal")
        cb.set_label("Brightness temperature (°C)", color="white", fontsize=7)
        cb.ax.xaxis.set_label_position("top")
        cb.ax.tick_params(colors="white", labelsize=7)
        cb.outline.set_edgecolor("white")

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=dpi, facecolor="black")
    plt.close(fig)
    return out
