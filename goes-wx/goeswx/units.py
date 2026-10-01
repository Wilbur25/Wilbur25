"""Unit conversion for display. Internally everything is °C, m/s, hPa, mm."""

from __future__ import annotations

import math


def temp(c, imperial: bool):
    if c is None or (isinstance(c, float) and math.isnan(c)):
        return None
    return c * 9 / 5 + 32 if imperial else c


def speed(ms, imperial: bool):
    if ms is None or (isinstance(ms, float) and math.isnan(ms)):
        return None
    return ms * 2.23694 if imperial else ms * 3.6


def precip(mm, imperial: bool):
    if mm is None or (isinstance(mm, float) and math.isnan(mm)):
        return None
    return mm / 25.4 if imperial else mm


def pressure(hpa, imperial: bool):
    if hpa is None or (isinstance(hpa, float) and math.isnan(hpa)):
        return None
    return hpa * 0.0295300 if imperial else hpa


def labels(imperial: bool) -> dict:
    return {"temp": "°F" if imperial else "°C", "speed": "mph" if imperial else "km/h",
            "precip": "in" if imperial else "mm", "pressure": "inHg" if imperial else "hPa"}


def compass(deg) -> str:
    if deg is None or (isinstance(deg, float) and math.isnan(deg)):
        return ""
    try:
        deg = float(deg)
    except (TypeError, ValueError):
        return str(deg)  # e.g. "VRB"
    return ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"][int((deg % 360) / 22.5 + 0.5) % 16]
