# Live signal bridge for the Raspberry Pi

`goes_signal_bridge.py` reads signal quality from your receiver software and serves the
GOES Dish Aligner page with a **Live signal** panel. The panel shows SNR, best-so-far, the
Viterbi error rate, Reed-Solomon fixes, packet rate, relative signal power and frequency
offset, and an optional tone whose pitch rises with SNR. Open the page on your phone while
you're at the dish.

```
dish → LNA/filter → SDR → Pi running goesrecv or SatDump
                                  │  stats
                          goes_signal_bridge.py  :8077
                                  │  Wi-Fi
                           phone browser → http://<pi-address>:8077
```

## Install

Copy the whole `goes-dish-aligner` folder to the Pi. The bridge serves `../index.html`.

```sh
git clone https://github.com/wilbur25/wilbur25.git
cd wilbur25/goes-dish-aligner/pi
pip3 install pynng          # only needed for goestools
```

On Raspberry Pi OS Bookworm, `pip3 install` may refuse to install system-wide. Use
`pip3 install --break-system-packages pynng` or a virtualenv.

## Run with goestools (goesrecv)

Make sure these sections are in your `goesrecv.conf`. They're in the example config that
ships with goestools, but some setup guides remove them:

```toml
[demodulator.stats_publisher]
bind = "tcp://0.0.0.0:6001"

[decoder.stats_publisher]
bind = "tcp://0.0.0.0:6002"

[clock_recovery.sample_publisher]
bind = "tcp://0.0.0.0:5002"
send_buffer = 2097152
```

Restart goesrecv, then:

```sh
python3 goes_signal_bridge.py --source goestools
```

What each figure comes from:

- **SNR** is estimated from the demodulated symbols (port 5002) with the M2M4 estimator.
- **Error rate** is goesrecv's per-packet Viterbi corrections divided by the 16,384 coded
  bits in a packet. The "bits corrected per packet" figure matches goesrecv's `vit(avg)`.
- **Signal power** is the inverse of the AGC gain. It's relative, so use it to compare
  positions, not as an absolute level.

If goesrecv runs on another machine, add `--goesrecv-host <its-address>`.

## Run with SatDump

Start SatDump's live HRIT pipeline with its web API turned on, for example:

```sh
satdump live goes_hrit /home/pi/goes --source rtlsdr --samplerate 2.4e6 \
  --frequency 1694.1e6 --gain 49 --http_server 0.0.0.0:8081
```

Then:

```sh
python3 goes_signal_bridge.py --source satdump --satdump-url http://127.0.0.1:8081/api
```

SatDump reports SNR, peak SNR, Viterbi BER, deframer lock and Reed-Solomon errors. It doesn't
report packet rate or AGC power, so those show as "—".

## Try it without hardware

```sh
python3 goes_signal_bridge.py --source demo
```

## Open it on your phone

Browse to `http://<pi-address>:8077`, for example `http://raspberrypi.local:8077` or
`http://192.168.1.50:8077`. The panel connects by itself. The phone needs to be on the same
network as the Pi.

The copy of the page published on claude.ai can't reach devices on your home network, so use
the page served by the Pi when you want live readings.

## Start at boot

```sh
sudo cp goes-signal-bridge.service /etc/systemd/system/
sudo nano /etc/systemd/system/goes-signal-bridge.service   # check the path, user and --source
sudo systemctl enable --now goes-signal-bridge
```

## Aligning with it

1. Set the dish to the azimuth, elevation and feed angle the page gives you.
2. Turn on **Tone**, then sweep the azimuth slowly. The pitch rises as you come onto the
   satellite.
3. Find the peak, note **Best so far**, and move past it until SNR falls a couple of dB.
   Then come back to the peak. Do the same for elevation, then the feed rotation.
4. Press **Reset best** whenever you start working on a new adjustment.

Readings average over a moment, so after each small move wait two or three seconds before
judging. For HRIT, a steady lock with SNR above about 4 dB will decode; 6 dB or more leaves
margin for rain.
