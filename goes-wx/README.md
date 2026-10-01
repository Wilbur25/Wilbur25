# goes-wx: a home weather station built on GOES East

This runs on your own Linux machine. It pulls data from NOAA's public AWS buckets and builds:

- **Satellite imagery** of your region from GOES-19 (GOES East): true color (with an infrared night view blended in), enhanced infrared, water vapor, and airmass RGB. GLM lightning flashes are drawn on top.
- **Timelapses**: MP4 loops of the last few hours of each image type.
- **Current conditions**: the latest observation from your nearest airport (METAR), plus what GOES sees directly overhead (cloud-top temperature, deep convection, lightning nearby).
- **A multi-day forecast**: GFS model output for your exact location, run through a **local correction model** that learns how GFS tends to be wrong at your station. It produces a 7-day outlook, an hourly table, a meteogram, and daily maps of high temperature and rainfall.
- **A dashboard**: one web page that shows all of the above. You can open it on any device on your home network.

## Quick start

```bash
sudo apt install python3-venv python3-pip ffmpeg
git clone https://github.com/Wilbur25/Wilbur25.git && cd Wilbur25/goes-wx
./deploy/install.sh          # creates config.yaml, then stops
nano config.yaml             # set your location, nearest airport (METAR id) and map region
./deploy/install.sh          # installs, schedules, and does a first run
```

Then open `http://<mini-itx-ip>:8080/`.

To run commands by hand, use `.venv/bin/goeswx <command>` from this folder:

| Command | What it does |
|---|---|
| `imagery [--hours N]` | Downloads new GOES scans and renders the frames. `--hours` catches up on the past N hours. |
| `timelapse` | Rebuilds the MP4 loops. |
| `current` | Fetches the latest station observation and combines it with the satellite reading overhead. |
| `forecast [--force]` | Builds the forecast from the newest GFS run. If that run is already processed, it does nothing unless you pass `--force`. |
| `backfill --days 14` | Downloads past GFS runs and station history, so the correction model can train right away. |
| `train` | Retrains the local correction model and prints its accuracy against raw GFS. |
| `tick` | Runs `imagery`, `timelapse`, `current` and `dashboard` in one go. This is what the 5-minute timer calls. |
| `serve` | Serves the dashboard on port 8080. |

### Schedule (systemd user timers)

| Timer | When | Job |
|---|---|---|
| `goeswx-tick` | Every 5 min | Imagery, timelapses, current conditions, dashboard |
| `goeswx-forecast` | Hourly | New forecast whenever a new GFS run is published (every 6 hours) |
| `goeswx-train` | Nightly | Retrains the local correction model |
| `goeswx-serve` | Always on | Dashboard web server |

To watch the logs: `journalctl --user -u goeswx-tick -u goeswx-forecast -f`

## How the forecast works

1. **GFS** is NOAA's global weather model. It runs four times a day, and each run's output files are about 500 MB. Each file comes with an `.idx` index, so goes-wx uses HTTP byte ranges to download only the ~1 MB fields it needs, such as 2 m temperature, wind, and rain. A 7-day forecast at 3-hour steps is 57 files. It takes about 20 seconds and downloads about 475 MB (14 fields of about 0.6 MB each, per step).
2. GFS predicts the weather on a 25 km grid. It doesn't see your local terrain, the river valley, or how the airport sensor is sited, so its errors at one spot are **systematic**. For example, it may be consistently too warm on clear, calm nights.
3. Every forecast and every hourly station observation is saved in `data/goeswx.db`. Each night, `train` fits a gradient-boosted model to the gap between what GFS predicted and what was observed. It uses as inputs the forecast lead time, time of day, season, cloud cover, wind and so on. It then adds the predicted error back to each new forecast, for temperature, dew point and wind speed.
4. A variable is corrected only if the correction **beats raw GFS on held-out runs**. The dashboard's Model panel shows the average error before and after, so you can see whether it is helping.

The model needs about 300 forecast/observation pairs from at least 10 GFS runs before it starts. That is about 3 days if you start from scratch. To start straight away, run `goeswx backfill --days 14` once. It downloads about 5 GB of past GFS data (two runs a day, 6-hourly steps), plus the station's history from the Iowa Environmental Mesonet. `--days 30` is about 10 GB.

## Data use and disk

- Imagery: each CONUS scan is about 63 MB. It is deleted once rendered. With the default 10-minute interval that is about **9 GB/day of downloads**; set `satellite.interval_minutes: 30` to cut it to a third.
- Frames are about 1 MB each per product and are kept for 72 hours (about 1.7 GB). `timelapse.keep_frames_hours` controls this.
- Forecast: about 475 MB per GFS run, so about 1.9 GB/day. Setting `forecast.resolution: 0p50` cuts this to roughly a quarter. If you switch, delete `data/mos.joblib` and retrain, so the correction model learns from the same grid.

## Outside the continental US

`ABI-L2-MCMIPC` covers the CONUS sector. For anywhere else in GOES East's view (the Americas and the Atlantic), set `satellite.product: ABI-L2-MCMIPF` (full disk, every 10 minutes, larger files). For the western US and the Pacific, use GOES West: `satellite.bucket: noaa-goes18`.

## Development

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

Implementation note: the GRIB library (`eccodes`) and the mapping library (`cartopy`) crash when both are loaded in one Python process. So GRIB decoding (`gfs.py`, `forecast.py`) never imports cartopy, and map rendering runs as a separate `goeswx forecast-maps` process.

## Roadmap

- **Your own ground station**: read files decoded by SatDump or goestools from the dish instead of from AWS. This needs adding HRIT/GRB file readers for the image pipeline.
- **The T1000 GPU**: nothing here needs it yet. The next step that would use it is an AI global weather model (for example GraphCast or Aurora) started from GFS initial conditions. Its 4 GB of VRAM is tight, so this needs testing.
- **GEFS ensemble**: forecast uncertainty ranges and rain chances.

Not an official forecast. For warnings, use the National Weather Service.
