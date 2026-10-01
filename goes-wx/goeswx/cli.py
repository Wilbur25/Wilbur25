"""Command line: goeswx <command>.

Modules are imported inside each command on purpose: the GRIB library (eccodes)
and the mapping library (cartopy) crash if loaded into the same process, so
commands that need both run the second half in a child process.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys

from . import config


def _child(cfg_path, *args) -> int:
    cmd = [sys.executable, "-m", "goeswx"] + (["-c", cfg_path] if cfg_path else []) + list(args)
    return subprocess.call(cmd)


def cmd_imagery(cfg, a):
    from . import imagery
    imagery.update(cfg, hours=a.hours)


def cmd_timelapse(cfg, a):
    from . import timelapse
    timelapse.build(cfg, hours=a.hours)


def cmd_current(cfg, a):
    from . import current
    current.update(cfg)


def cmd_forecast(cfg, a):
    from . import forecast
    result = forecast.run(cfg, force=a.force)
    if (result is not None or a.force) and cfg["forecast"]["maps"]:
        _child(a.config, "forecast-maps")


def cmd_forecast_maps(cfg, a):
    from . import forecast_maps
    forecast_maps.render(cfg)


def cmd_train(cfg, a):
    from . import mos, store
    print(store.counts(cfg.data_dir / "goeswx.db"))
    report = mos.train(cfg)
    for k, v in report["targets"].items():
        print(f"  {k:5s} {v}")


def cmd_backfill(cfg, a):
    from . import forecast
    forecast.backfill(cfg, a.days)


def cmd_dashboard(cfg, a):
    from . import dashboard
    dashboard.build(cfg)


def cmd_tick(cfg, a):
    """Everything the 5-minute timer does. Each step is isolated so one failure doesn't stop the rest."""
    rc = 0
    for step in ("imagery", "timelapse", "current", "dashboard"):
        rc |= _child(a.config, step)
    return rc


def cmd_serve(cfg, a):
    import functools
    import http.server

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(cfg.output_dir))
    with http.server.ThreadingHTTPServer((a.host, a.port), handler) as httpd:
        print(f"Serving {cfg.output_dir} at http://{a.host}:{a.port}/")
        httpd.serve_forever()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="goeswx", description=__doc__.splitlines()[0])
    p.add_argument("-c", "--config", help="path to config.yaml (default: ./config.yaml or $GOESWX_CONFIG)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("imagery", help="download new GOES scans and render frames")
    s.add_argument("--hours", type=float, help="catch up on the last N hours instead of just the newest scan")
    s.set_defaults(fn=cmd_imagery)
    s = sub.add_parser("timelapse", help="build MP4 loops from rendered frames")
    s.add_argument("--hours", type=float)
    s.set_defaults(fn=cmd_timelapse)
    sub.add_parser("current", help="fetch the station observation; combine with satellite").set_defaults(fn=cmd_current)
    s = sub.add_parser("forecast", help="build the multi-day forecast from the newest GFS run")
    s.add_argument("--force", action="store_true", help="rebuild even if this GFS run was already done")
    s.set_defaults(fn=cmd_forecast)
    sub.add_parser("forecast-maps", help="render daily forecast maps").set_defaults(fn=cmd_forecast_maps)
    sub.add_parser("train", help="train the local correction model").set_defaults(fn=cmd_train)
    s = sub.add_parser("backfill", help="download past GFS runs and station history for training")
    s.add_argument("--days", type=int, default=14)
    s.set_defaults(fn=cmd_backfill)
    sub.add_parser("dashboard", help="write the dashboard page").set_defaults(fn=cmd_dashboard)
    sub.add_parser("tick", help="imagery + timelapse + current + dashboard").set_defaults(fn=cmd_tick)
    s = sub.add_parser("serve", help="serve the dashboard over HTTP")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8080)
    s.set_defaults(fn=cmd_serve)

    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    cfg = config.load(a.config)
    return a.fn(cfg, a) or 0


if __name__ == "__main__":
    sys.exit(main())
