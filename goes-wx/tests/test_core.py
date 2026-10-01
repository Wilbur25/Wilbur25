from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from goeswx import aws, config, gfs, imagery, mos, store
from goeswx.solar import solar_zenith

UTC = timezone.utc

IDX = """\
1:0:d=2026100106:PRMSL:mean sea level:24 hour fcst:
2:100:d=2026100106:TMP:2 m above ground:24 hour fcst:
3:250:d=2026100106:TMAX:2 m above ground:18-24 hour max fcst:
4:400:d=2026100106:APCP:surface:18-24 hour acc fcst:
5:500:d=2026100106:APCP:surface:0-1 day acc fcst:
6:650:d=2026100106:TCDC:entire atmosphere:18-24 hour ave fcst:
7:700:d=2026100106:TCDC:entire atmosphere:24 hour fcst:
"""


def test_parse_time_range():
    assert gfs.parse_time_range("anl") == ("inst", 0, 0)
    assert gfs.parse_time_range("24 hour fcst") == ("inst", 24, 24)
    assert gfs.parse_time_range("18-24 hour max fcst") == ("max", 18, 24)
    assert gfs.parse_time_range("0-1 day acc fcst") == ("acc", 0, 24)
    assert gfs.parse_time_range("something else") is None


def test_idx_select_picks_right_messages():
    entries = gfs.parse_idx(IDX)
    sel = gfs.select(entries, 24, ["t2m", "tmax", "apcp", "tcc", "mslp"])
    assert (sel["t2m"].offset, sel["t2m"].end) == (100, 249)
    assert sel["tmax"].offset == 250
    assert sel["apcp"].offset == 500          # run total, not the 6-hour bucket
    assert sel["tcc"].offset == 700           # instantaneous, not the average
    assert sel["tcc"].end is None             # last message reads to end of file
    assert gfs.select(entries, 27, ["t2m"]) == {}


def test_steps_for():
    s = gfs.steps_for(7, 3)
    assert s[0] == 0 and s[-1] == 168 and len(s) == 57
    hourly = gfs.steps_for(7, 1)
    assert 119 in hourly and 121 not in hourly and 123 in hourly and hourly[-1] == 168


def test_grid_point_bilinear_and_wrap():
    lat = 90 - 0.25 * np.arange(721)
    lon = 0.25 * np.arange(1440)
    vals = lat[:, None] + 0 * lon[None, :] + np.where(lon[None, :] >= 180, lon[None, :] - 360, lon[None, :]) * 0.001
    g = gfs.Grid(vals, 90.0, 0.0, 0.25, 0.25)
    assert abs(g.point(39.1, -94.5) - (39.1 - 0.0945)) < 1e-6
    lats, lons, sub = g.crop([-100, -90, 35, 40])
    assert lons.min() < -100 and lons.max() > -90 and np.all(np.diff(lons) > 0)
    assert sub.shape == (len(lats), len(lons))


def test_goes_scan_start():
    key = "ABI-L2-MCMIPC/2026/274/18/OR_ABI-L2-MCMIPC-M6_G19_s20262741801173_e20262741803546_c1.nc"
    assert aws.goes_scan_start(key) == datetime(2026, 10, 1, 18, 1, 17, 300000, tzinfo=UTC)


def test_solar_zenith_sane():
    noon = solar_zenith(np.array(0.0), np.array(0.0), datetime(2026, 3, 20, 12, 7, tzinfo=UTC))
    midnight = solar_zenith(np.array(0.0), np.array(0.0), datetime(2026, 3, 20, 0, 0, tzinfo=UTC))
    assert float(noon) < 2 and float(midnight) > 170


def test_pick_scans_thins_newest_first():
    t0 = datetime(2026, 10, 1, 18, 1, tzinfo=UTC)
    files = [(t0 + timedelta(minutes=5 * i), f"k{i}", 1) for i in range(7)]   # 18:01 .. 18:31
    picked = imagery._pick_scans(files, 10, done={"20261001T1821"})
    assert [t.strftime("%H%M") for t, _, _ in picked] == ["1801", "1811", "1831"]


def _cfg(tmp_path):
    (tmp_path / "config.yaml").write_text("paths: {data: ./d, output: ./o}\n")
    return config.load(tmp_path / "config.yaml")


def test_mos_learns_a_station_bias(tmp_path):
    cfg = _cfg(tmp_path)
    db = cfg.data_dir / "goeswx.db"
    rng = np.random.default_rng(0)
    fc, ob = [], []
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for r in range(40):
        run = start + timedelta(hours=12 * r)
        for lead in range(0, 72, 3):
            valid = run + timedelta(hours=lead)
            t = 10 + 8 * np.sin(2 * np.pi * (valid.hour - 9) / 24)
            fc.append({"run": run, "valid": valid, "lead": lead, "t2m": t, "d2m": t - 5,
                       "wspd": 4.0, "gust": 7.0, "mslp": 1013.0, "tcc": 50.0, "precip": 0.0})
    for h in range(0, 40 * 12 + 72):
        valid = start + timedelta(hours=h)
        t = 10 + 8 * np.sin(2 * np.pi * (valid.hour - 9) / 24)
        night = valid.hour < 12
        ob.append({"valid": valid, "t2m": t - (3 if night else 0) + rng.normal(0, 0.3),
                   "d2m": t - 5, "wspd": 3.0, "gust": None, "mslp": 1013.0})
    store.save_forecasts(db, fc)
    store.save_obs(db, ob, "test")
    report = mos.train(cfg)
    t = report["targets"]["t2m"]
    assert t["status"] == "active" and t["mae_local"] < t["mae_gfs"] / 2

    df = pd.DataFrame(fc[:24])
    out, corrected = mos.apply(cfg, df)
    assert "t2m" in corrected
    night = pd.to_datetime(out["valid"]).dt.hour < 12
    assert ((out["t2m_gfs"] - out["t2m"])[night].mean()) > 2


def test_mos_waits_for_data(tmp_path):
    cfg = _cfg(tmp_path)
    report = mos.train(cfg)
    assert report["targets"]["t2m"]["status"] == "no data yet"
    df = pd.DataFrame([{"valid": datetime.now(UTC), "lead": 0, "t2m": 1.0}])
    assert mos.apply(cfg, df)[1] == []
