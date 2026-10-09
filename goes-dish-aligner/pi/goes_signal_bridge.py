#!/usr/bin/env python3
"""Live signal bridge for the GOES Dish Aligner.

Runs on the Raspberry Pi next to your receiver software and serves:

  /                 the dish aligner page (../index.html), with the live panel enabled
  /api/signal       the latest signal snapshot as JSON
  /api/stream       the same snapshot pushed ~4 times a second (Server-Sent Events)

Sources:
  goestools  reads goesrecv's nanomsg publishers (needs `pip install pynng`)
  satdump    polls SatDump's HTTP API (start SatDump with --http_server 0.0.0.0:8081)
  demo       synthetic signal that rises and falls, for trying the page without hardware

Examples:
  python3 goes_signal_bridge.py --source goestools
  python3 goes_signal_bridge.py --source satdump --satdump-url http://127.0.0.1:8081/api
  python3 goes_signal_bridge.py --source demo
"""

import argparse
import array
import collections
import json
import math
import os
import random
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# A goestools packet is a 1024-byte CADU, rate-1/2 convolutionally encoded.
ENCODED_FRAME_BITS = 1024 * 8 * 2
WINDOW_S = 2.0  # rolling window for packet-based figures


class Snapshot:
    """Thread-safe holder for the latest figures."""

    def __init__(self, source):
        self._lock = threading.Lock()
        self._data = {
            "source": source,
            "connected": False,
            "updated": None,
            "lock": None,
            "snr_db": None,
            "peak_snr_db": None,
            "ber": None,
            "viterbi_per_packet": None,
            "rs_errors": None,
            "packets_per_s": None,
            "drop_rate": None,
            "power_db": None,
            "freq_offset_hz": None,
            "message": "Starting…",
        }

    def update(self, **kw):
        with self._lock:
            self._data.update(kw)
            self._data["updated"] = time.time()

    def get(self):
        with self._lock:
            return dict(self._data)


# ---------------------------------------------------------------- goestools

def m2m4_snr_db(iq):
    """Blind SNR estimate for PSK symbols (M2M4 estimator). iq: list of complex."""
    if len(iq) < 64:
        return None
    m2 = sum(abs(z) ** 2 for z in iq) / len(iq)
    m4 = sum(abs(z) ** 4 for z in iq) / len(iq)
    s2 = 2 * m2 * m2 - m4
    if s2 <= 0:
        return 0.0
    s = math.sqrt(s2)
    n = m2 - s
    if n <= 0:
        return 30.0
    return max(0.0, 10 * math.log10(s / n))


def run_goestools(snap, host, demod_port, decoder_port, samples_port):
    try:
        import pynng
    except ImportError:
        snap.update(message="pynng is not installed. Run: pip3 install pynng")
        return

    packets = collections.deque()  # (time, viterbi_bits, rs_bytes, ok)
    state = {"peak": None, "last_decoder": 0.0, "last_demod": 0.0}
    plock = threading.Lock()

    def sub(port):
        s = pynng.Sub0(recv_timeout=1500)
        s.subscribe(b"")
        s.dial(f"tcp://{host}:{port}", block=False)  # keeps retrying until goesrecv is up
        return s

    def decoder_loop():
        s = sub(decoder_port)
        while True:
            try:
                msg = json.loads(s.recv().decode())
            except pynng.Timeout:
                continue
            except (ValueError, UnicodeDecodeError):
                continue
            now = time.time()
            with plock:
                packets.append((now, msg.get("viterbi_errors", 0), msg.get("reed_solomon_errors", 0), bool(msg.get("ok"))))
            state["last_decoder"] = now

    def demod_loop():
        s = sub(demod_port)
        while True:
            try:
                msg = json.loads(s.recv().decode())
            except pynng.Timeout:
                continue
            except (ValueError, UnicodeDecodeError):
                continue
            gain = float(msg.get("gain", 0) or 0)
            state["last_demod"] = time.time()
            snap.update(
                # AGC gain goes down as the signal gets stronger, so its inverse tracks input power.
                power_db=round(-20 * math.log10(gain), 2) if gain > 0 else None,
                freq_offset_hz=round(float(msg.get("frequency", 0)), 1),
            )

    def samples_loop():
        s = sub(samples_port)
        snr_avg, last = None, 0.0
        while True:
            try:
                raw = s.recv()
            except pynng.Timeout:
                continue
            # goesrecv sends hundreds of blocks a second; measuring four a second is plenty
            # and keeps the Pi's CPU free for decoding.
            now = time.time()
            if now - last < 0.25:
                continue
            last = now
            # Interleaved int8 I/Q, scaled by 127 in goesrecv.
            v = array.array("b", raw[: 2 * 4096])
            iq = [complex(v[i], v[i + 1]) / 127.0 for i in range(0, len(v) - 1, 2)]
            snr = m2m4_snr_db(iq)
            if snr is None:
                continue
            snr_avg = snr if snr_avg is None else 0.8 * snr_avg + 0.2 * snr
            if state["peak"] is None or snr_avg > state["peak"]:
                state["peak"] = snr_avg
            snap.update(snr_db=round(snr_avg, 2), peak_snr_db=round(state["peak"], 2))

    for fn in (decoder_loop, demod_loop, samples_loop):
        threading.Thread(target=fn, daemon=True).start()

    while True:
        time.sleep(0.25)
        now = time.time()
        with plock:
            while packets and now - packets[0][0] > WINDOW_S:
                packets.popleft()
            window = list(packets)
        connected = now - max(state["last_decoder"], state["last_demod"]) < 3
        if window:
            ok = [p for p in window if p[3]]
            vit = sum(p[1] for p in window) / len(window)
            rs_vals = [p[2] for p in ok if p[2] >= 0]
            snap.update(
                connected=True,
                lock=len(ok) > 0,
                viterbi_per_packet=round(vit, 1),
                ber=round(vit / ENCODED_FRAME_BITS, 5),
                rs_errors=round(sum(rs_vals) / len(rs_vals), 2) if rs_vals else None,
                packets_per_s=round(len(ok) / WINDOW_S, 1),
                drop_rate=round(1 - len(ok) / len(window), 3),
                message="Receiving from goesrecv",
            )
        else:
            snap.update(
                connected=connected,
                lock=False if connected else None,
                packets_per_s=0.0 if connected else None,
                message="goesrecv running, no packets yet (not locked)" if connected
                else f"Waiting for goesrecv on {host} (ports {demod_port}/{decoder_port}/{samples_port})",
            )


# ---------------------------------------------------------------- SatDump

def walk(obj, path=()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, path + (k,))
    else:
        yield path, obj


def run_satdump(snap, url):
    while True:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                data = json.load(r)
        except Exception as e:  # noqa: BLE001 - any failure just means "not reachable yet"
            snap.update(connected=False, lock=None, message=f"Waiting for SatDump at {url} ({e.__class__.__name__})")
            time.sleep(1)
            continue
        found = {}
        for path, val in walk(data):
            key = path[-1] if path else ""
            if key in ("snr", "peak_snr", "freq", "viterbi_ber", "viterbi_lock", "deframer_lock", "rs_avg", "deframer_state"):
                found.setdefault(key, val)
        lock = None
        if "deframer_lock" in found:
            lock = bool(found["deframer_lock"])
        elif "deframer_state" in found:
            lock = found["deframer_state"] == "SYNCED"
        ber = found.get("viterbi_ber")
        snap.update(
            connected=True,
            lock=lock,
            snr_db=round(float(found["snr"]), 2) if "snr" in found else None,
            peak_snr_db=round(float(found["peak_snr"]), 2) if "peak_snr" in found else None,
            freq_offset_hz=round(float(found["freq"]), 1) if "freq" in found else None,
            ber=round(float(ber), 5) if ber is not None else None,
            viterbi_per_packet=round(float(ber) * ENCODED_FRAME_BITS, 1) if ber is not None else None,
            rs_errors=found.get("rs_avg"),
            message="Receiving from SatDump" if found else "SatDump is running but no demodulator stats were found. Is a live pipeline running?",
        )
        time.sleep(0.5)


# ---------------------------------------------------------------- demo

def run_demo(snap):
    t0, peak = time.time(), 0.0
    while True:
        t = time.time() - t0
        # A slow sweep across the beam: signal peaks every 40 s.
        offset = 6 * math.sin(2 * math.pi * t / 40)
        snr = max(0.0, 11 * math.exp(-(offset / 3.2) ** 2) + random.gauss(0, 0.25))
        peak = max(peak, snr)
        lock = snr > 3.5
        ber = min(0.08, 0.5 * math.exp(-snr / 1.6)) if snr > 0.5 else None
        snap.update(
            connected=True, lock=lock, snr_db=round(snr, 2), peak_snr_db=round(peak, 2),
            ber=round(ber, 5) if ber else None,
            viterbi_per_packet=round(ber * ENCODED_FRAME_BITS, 1) if ber else None,
            rs_errors=round(max(0.0, 6 - snr) * 1.5, 2) if lock else None,
            packets_per_s=round(random.uniform(48, 52), 1) if lock else 0.0,
            drop_rate=round(max(0.0, (5.5 - snr) / 2), 3) if lock else 1.0,
            power_db=round(-38 + snr * 0.8 + random.gauss(0, 0.15), 2),
            freq_offset_hz=round(-1450 + random.gauss(0, 15), 1),
            message="Demo data (no receiver connected)",
        )
        time.sleep(0.25)


# ---------------------------------------------------------------- HTTP

def make_handler(snap, page_path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _cors(self):
            # Lets a copy of the page opened from disk (file://) or another host connect.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                try:
                    with open(page_path, "rb") as f:
                        body = f.read()
                except OSError:
                    self.send_error(404, "index.html not found next to the pi/ folder")
                    return
                if not body.lstrip().lower().startswith(b"<!doctype"):
                    body = (b'<!doctype html><html><head><meta charset="utf-8">'
                            b'<meta name="viewport" content="width=device-width,initial-scale=1"></head><body>'
                            + body + b"</body></html>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._cors()
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/signal":
                body = json.dumps(snap.get()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self._cors()
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/stream":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self._cors()
                self.end_headers()
                try:
                    while True:
                        self.wfile.write(b"data: " + json.dumps(snap.get()).encode() + b"\n\n")
                        self.wfile.flush()
                        time.sleep(0.25)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self.send_error(404)

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["goestools", "satdump", "demo"], default="goestools")
    ap.add_argument("--port", type=int, default=8077, help="port this bridge listens on (default 8077)")
    ap.add_argument("--goesrecv-host", default="127.0.0.1")
    ap.add_argument("--demod-port", type=int, default=6001)
    ap.add_argument("--decoder-port", type=int, default=6002)
    ap.add_argument("--samples-port", type=int, default=5002)
    ap.add_argument("--satdump-url", default="http://127.0.0.1:8081/api")
    args = ap.parse_args()

    snap = Snapshot(args.source)
    if args.source == "goestools":
        target = lambda: run_goestools(snap, args.goesrecv_host, args.demod_port, args.decoder_port, args.samples_port)
    elif args.source == "satdump":
        target = lambda: run_satdump(snap, args.satdump_url)
    else:
        target = lambda: run_demo(snap)
    threading.Thread(target=target, daemon=True).start()

    page = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "index.html")
    server = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(snap, page))
    server.daemon_threads = True
    print(f"GOES signal bridge ({args.source}) on http://0.0.0.0:{args.port}  -  open http://<pi-address>:{args.port} on your phone")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
