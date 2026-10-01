"""Anonymous access to NOAA's public S3 buckets over plain HTTPS."""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
_session = requests.Session()
_session.headers["User-Agent"] = "goeswx/0.1 (home weather station)"


def bucket_url(bucket: str) -> str:
    return f"https://{bucket}.s3.amazonaws.com"


def _get(url: str, *, headers=None, params=None, timeout=60, retries=4) -> requests.Response:
    delay = 2
    for attempt in range(retries + 1):
        try:
            r = _session.get(url, headers=headers, params=params, timeout=timeout)
            if r.status_code < 500:
                r.raise_for_status()
                return r
        except (requests.ConnectionError, requests.Timeout):
            if attempt == retries:
                raise
        if attempt == retries:
            r.raise_for_status()
        time.sleep(delay)
        delay *= 2
    raise RuntimeError("unreachable")


def list_keys(bucket: str, prefix: str) -> list[tuple[str, int]]:
    """Return (key, size) for every object under prefix."""
    out = []
    token = None
    while True:
        params = {"list-type": "2", "prefix": prefix}
        if token:
            params["continuation-token"] = token
        root = ET.fromstring(_get(bucket_url(bucket) + "/", params=params).content)
        for c in root.findall("s3:Contents", _NS):
            out.append((c.find("s3:Key", _NS).text, int(c.find("s3:Size", _NS).text)))
        if root.findtext("s3:IsTruncated", namespaces=_NS) != "true":
            return out
        token = root.findtext("s3:NextContinuationToken", namespaces=_NS)


def download(bucket: str, key: str, dest: Path) -> Path:
    """Download an object to dest (atomically), skipping if it already exists."""
    dest = Path(dest)
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with _session.get(f"{bucket_url(bucket)}/{key}", stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    tmp.rename(dest)
    return dest


def get_bytes(bucket: str, key: str) -> bytes:
    return _get(f"{bucket_url(bucket)}/{key}").content


def get_text(bucket: str, key: str) -> str:
    return _get(f"{bucket_url(bucket)}/{key}").text


def get_range(bucket: str, key: str, start: int, end: int | None) -> bytes:
    """Fetch bytes [start, end] inclusive (end=None means to end of object)."""
    rng = f"bytes={start}-" + ("" if end is None else str(end))
    return _get(f"{bucket_url(bucket)}/{key}", headers={"Range": rng}).content


# --- GOES file naming -------------------------------------------------------

_GOES_TIME = re.compile(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})(\d)")


def goes_scan_start(key: str) -> datetime:
    """Parse the scan start time from a GOES-R filename (…_sYYYYJJJHHMMSSt_…)."""
    m = _GOES_TIME.search(key)
    if not m:
        raise ValueError(f"not a GOES-R filename: {key}")
    y, doy, hh, mm, ss, tenth = (int(g) for g in m.groups())
    return datetime(y, 1, 1, hh, mm, ss, tenth * 100000, tzinfo=timezone.utc) + timedelta(days=doy - 1)


def goes_hour_prefix(product: str, t: datetime) -> str:
    return f"{product}/{t:%Y}/{t.timetuple().tm_yday:03d}/{t:%H}/"


def goes_files_between(bucket: str, product: str, start: datetime, end: datetime) -> list[tuple[datetime, str, int]]:
    """List (scan_start, key, size) for files whose scan starts in [start, end], oldest first."""
    hour = start.replace(minute=0, second=0, microsecond=0)
    out = []
    while hour <= end:
        for key, size in list_keys(bucket, goes_hour_prefix(product, hour)):
            t = goes_scan_start(key)
            if start <= t <= end:
                out.append((t, key, size))
        hour += timedelta(hours=1)
    return sorted(out)
