# Energie collector (Raspberry Pi / Odroid)

A small program — Python standard library only — that runs around the clock:

- every 60 s it reads the **cumulative energy counter** of every Shelly device (Gen1, Gen2/Plus, Gen3, Pro 3EM)
- stores it in SQLite; the counter is stored, not the power, so an outage of the collector or a restart loses no kWh
- optionally counts **gas meter pulses** on a GPIO pin (Raspberry Pi)
- serves an HTTP API on port 8600 from which the Energie app on the PC fetches new data

## Where to run it

| | Raspberry Pi | Odroid HC4 (NAS) |
|---|---|---|
| runs 24/7 | yes (one more device, ~2–4 W) | already does |
| gas meter on GPIO | **yes** (reed contact straight on the Pi) | no (needs a Shelly Uni or an ESP) |
| storage | SD card is fine for a write a minute; a USB stick/SSD is better | HDD ✓ |
| separate from the NAS | ✓ experiments cannot harm the NAS | shares the system with the NAS data |

Recommended: a Pi in the basement next to the meters (if there is LAN or Wi-Fi) as the "energy server";
the Odroid keeps a nightly backup of the database (below). If the Pi cannot sit near the gas meter, the
Odroid does the same job — the code is the same.

## Install (Raspberry Pi OS Bookworm / Armbian, Python 3.11+)

Automated from the PC with Ansible — system updates, time zone, the service and the backup in one
step, repeatable: see [deploy/README.md](../deploy/README.md) (`make deploy`). By hand:

```sh
scp -r collector pi@energy-pi:~/
ssh pi@energy-pi
cd ~/collector
python3 collector.py discover 192.168.178.0/24     # prints a [devices] block with names and IPs
cp collector.toml.example collector.toml           # paste the [devices] block from discover
python3 collector.py once                          # one test read
sudo sh install.sh                                 # systemd service (copies collector.toml too)
curl http://localhost:8600/api/health
```

Give the plugs a fixed IP in the router. Gen2+ devices need local HTTP enabled (the default). Devices
with a password set are not supported yet.

Then enter the address on the app's Settings tab (`http://energy-pi:8600`) or set
`ENERGIE_COLLECTOR_URL`; the app syncs on every start and from the Collector tab.

## Gas meter on GPIO (optional)

Many gas meters have a slot for a pulse sensor under the counter (printed e.g. "1 imp = 0,1 m³").
Wire a reed contact (or the meter maker's pulse sensor) between **GPIO17 and GND**, then in `collector.toml`:

```toml
[gas]
enabled = true
pin = 17
m3_per_pulse = 0.1
```
and calibrate once to the current reading (read it off the counter):
```sh
sudo -u energie python3 /opt/energie-collector/collector.py calibrate-gas 12345.678
```
At 0.1 m³ (≈ 1 kWh) per pulse and ~1 m³ a day there are about 10 pulses a day: enough for day/night,
coarse for hours. The meter belongs to the network operator — only place the sensor in its slot.

## API

| Endpoint | Returns |
|---|---|
| `GET /api/health` | device state (power, counter, errors) and the gas meter |
| `GET /api/samples?since=YYYY-MM-DD HH:MM:SS&limit=20000` | samples `[ts, name, total_wh, apower_w]` |
| `GET /api/gas?since=…` | the gas reading after every pulse `[ts, m3]` |

No authentication — for the home network only. Never forward port 8600 to the internet.

## Backup to the NAS (cron on the Pi)

```sh
sudo apt install sqlite3
# /etc/cron.d/energie-backup - every night at 3:15
15 3 * * * energie sqlite3 /opt/energie-collector/collector.db ".backup /mnt/nas/backup/collector-$(date +\%a).db"
```

## Data volume

10 devices × 1 sample per minute ≈ 14,000 rows a day ≈ 0.5 MB a day ≈ 200 MB a year. With
`interval_s = 300`, five times less.

---

© 2026 Taras Bulubas. All rights reserved. See [LICENSE](../LICENSE).
