"""Sun position (pure numpy, safe to import anywhere)."""

from __future__ import annotations

from datetime import datetime

import numpy as np


def solar_zenith(lat: np.ndarray, lon: np.ndarray, t: datetime) -> np.ndarray:
    """Approximate solar zenith angle in degrees (NOAA low-precision formulas)."""
    doy = t.timetuple().tm_yday
    hour = t.hour + t.minute / 60 + t.second / 3600
    g = 2 * np.pi / 365 * (doy - 1 + (hour - 12) / 24)
    decl = (0.006918 - 0.399912 * np.cos(g) + 0.070257 * np.sin(g) - 0.006758 * np.cos(2 * g)
            + 0.000907 * np.sin(2 * g) - 0.002697 * np.cos(3 * g) + 0.00148 * np.sin(3 * g))
    eqtime = 229.18 * (0.000075 + 0.001868 * np.cos(g) - 0.032077 * np.sin(g)
                       - 0.014615 * np.cos(2 * g) - 0.040849 * np.sin(2 * g))
    tst = hour * 60 + eqtime + 4 * lon               # true solar time, minutes
    ha = np.radians(tst / 4 - 180)
    la = np.radians(lat)
    cosz = np.sin(la) * np.sin(decl) + np.cos(la) * np.cos(decl) * np.cos(ha)
    return np.degrees(np.arccos(np.clip(cosz, -1, 1)))
