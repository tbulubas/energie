#!/usr/bin/env python3
# Copyright (c) 2026 Taras Bulubas. All rights reserved.
# Use requires prior written permission; see LICENSE.

"""Energie collector - runs around the clock on a Raspberry Pi or an Odroid.

  python3 collector.py discover 192.168.178.0/24   # find the Shelly devices on the network
  python3 collector.py run                         # collect + HTTP API (systemd)
  python3 collector.py calibrate-gas 12345.678     # set the current gas meter reading
  python3 collector.py once                        # read once and print (test)

Standard library of Python 3.11+ only (tomllib). For the gas meter on GPIO: `sudo apt install python3-gpiozero`.

What it does:
  - every N seconds it reads the cumulative energy counter of every Shelly (Gen1/Gen2/Gen3, plugs and 3EM)
  - stores it in SQLite (WAL); the COUNTER is stored, not the power, so an outage of the collector loses no kWh
  - optionally counts gas meter pulses on a GPIO pin (reed contact)
  - serves an HTTP API (port 8600) from which the Energie app on the PC fetches the data
"""
from __future__ import annotations

import ipaddress
import json
import sqlite3
import sys
import threading
import time
import tomllib
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
CFG = tomllib.loads((HERE / "collector.toml").read_text()) if (HERE / "collector.toml").exists() else {}
DB = Path(CFG.get("db", HERE / "collector.db"))
INTERVAL = int(CFG.get("interval_s", 60))
PORT = int(CFG.get("port", 8600))
DEVICES: dict[str, str] = CFG.get("devices", {})          # name -> IP/hostname
GAS = CFG.get("gas", {})

STATE: dict[str, dict] = {n: {"name": n, "ip": ip} for n, ip in DEVICES.items()}
GAS_STATE = {"pulses": 0, "last_pulse": None}
DB_LOCK = threading.Lock()


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def http_json(url: str, timeout: float = 4.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


# ---------- DB ----------

def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS sample (ts TEXT NOT NULL, name TEXT NOT NULL, total_wh REAL NOT NULL,
                                           apower_w REAL, PRIMARY KEY (ts, name));
        CREATE INDEX IF NOT EXISTS idx_sample_ts ON sample(ts);
        CREATE TABLE IF NOT EXISTS gas_pulse (ts TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS gas_calib (ts TEXT NOT NULL, m3 REAL NOT NULL);
    """)
    return con


# ---------- Shelly ----------

def detect_gen(host: str) -> int:
    info = http_json(f"http://{host}/shelly", timeout=2)
    return int(info.get("gen", 1))


def read_device(host: str, gen: int) -> tuple[float, float | None]:
    """Return (cumulative energy Wh, current power W), summed over all channels/phases."""
    if gen >= 2:
        st = http_json(f"http://{host}/rpc/Shelly.GetStatus")
        total, power, found = 0.0, 0.0, False
        for key, comp in st.items():
            if not isinstance(comp, dict):
                continue
            if key.startswith(("switch:", "pm1:", "em1:", "light:", "cover:")) and "aenergy" in comp:
                total += comp["aenergy"]["total"]
                power += comp.get("apower") or comp.get("act_power") or 0
                found = True
            elif key.startswith("emdata:"):                      # Pro 3EM
                total += comp.get("total_act", 0)
                found = True
            elif key.startswith("em1data:"):                     # Pro EM
                total += comp.get("total_act_energy", 0)
                found = True
            elif key.startswith("em:"):
                power += comp.get("total_act_power", 0)
        if not found:
            raise ValueError("device reports no energy (aenergy/emdata)")
        return total, power
    st = http_json(f"http://{host}/status")
    if "meters" in st:                                          # Gen1 plug: total in watt-minutes
        return sum(m.get("total", 0) for m in st["meters"]) / 60, sum(m.get("power", 0) for m in st["meters"])
    if "emeters" in st:                                         # Gen1 EM/3EM: total in Wh
        return sum(m.get("total", 0) for m in st["emeters"]), sum(m.get("power", 0) for m in st["emeters"])
    raise ValueError("unknown Gen1 status")


def poll_once(con: sqlite3.Connection) -> None:
    ts = now()
    for name, host in DEVICES.items():
        s = STATE[name]
        try:
            if "gen" not in s:
                s["gen"] = detect_gen(host)
            total, power = read_device(host, s["gen"])
            with DB_LOCK:
                con.execute("INSERT OR REPLACE INTO sample VALUES (?,?,?,?)", (ts, name, total, power))
            s.update(total_wh=total, apower_w=power, last_ok=ts, error=None)
        except Exception as e:  # noqa: BLE001 - a device offline must not stop the others
            s.update(error=f"{type(e).__name__}: {e}", last_err=ts)
            s.pop("gen", None)                                  # detect again on the next try
    with DB_LOCK:
        con.commit()


def poll_loop(con: sqlite3.Connection) -> None:
    while True:
        t0 = time.monotonic()
        poll_once(con)
        time.sleep(max(1.0, INTERVAL - (time.monotonic() - t0)))


# ---------- gas meter (GPIO) ----------

def gas_total(con) -> tuple[float | None, list[tuple[str, float]]]:
    """Calibrated reading, and (ts, m3) after every pulse since the last calibration."""
    cal = con.execute("SELECT ts, m3 FROM gas_calib ORDER BY ts DESC LIMIT 1").fetchone()
    if not cal:
        return None, []
    per = float(GAS.get("m3_per_pulse", 0.1))
    rows = con.execute("SELECT ts FROM gas_pulse WHERE ts > ? ORDER BY ts", (cal[0],)).fetchall()
    series = [(ts, round(cal[1] + (i + 1) * per, 3)) for i, (ts,) in enumerate(rows)]
    return (series[-1][1] if series else cal[1]), [(cal[0], cal[1])] + series


def start_gas(con) -> None:
    if not GAS.get("enabled"):
        return
    from gpiozero import Button                                 # imported only when gas is enabled
    btn = Button(int(GAS["pin"]), pull_up=True, bounce_time=float(GAS.get("debounce_s", 0.1)))

    def pulse():
        ts = now()
        with DB_LOCK:
            con.execute("INSERT INTO gas_pulse VALUES (?)", (ts,))
            con.commit()
        GAS_STATE["pulses"] += 1
        GAS_STATE["last_pulse"] = ts
    btn.when_pressed = pulse
    GAS_STATE["button"] = btn                                   # keep a reference
    print(f"[gas] GPIO{GAS['pin']} active, {GAS.get('m3_per_pulse', 0.1)} m3 per pulse")


# ---------- HTTP API ----------

class Api(BaseHTTPRequestHandler):
    con: sqlite3.Connection = None  # set in run()

    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        since = q.get("since", "2000-01-01 00:00:00")
        if u.path == "/api/health":
            gas = None
            if GAS.get("enabled"):
                with DB_LOCK:
                    m3, _ = gas_total(self.con)
                gas = {"pin": GAS["pin"], "m3": m3, "last_pulse": GAS_STATE["last_pulse"]}
            return self._send({"ok": True, "time": now(), "interval_s": INTERVAL,
                               "devices": list(STATE.values()), "gas": gas})
        if u.path == "/api/samples":
            limit = min(int(q.get("limit", 20000)), 100000)
            with DB_LOCK:
                rows = self.con.execute("SELECT ts, name, total_wh, apower_w FROM sample WHERE ts > ? "
                                        "ORDER BY ts, name LIMIT ?", (since, limit)).fetchall()
            return self._send({"rows": rows})
        if u.path == "/api/gas":
            with DB_LOCK:
                _, series = gas_total(self.con)
            return self._send({"rows": [r for r in series if r[0] > since]})
        self._send({"error": "not found"}, 404)


# ---------- CLI ----------

def discover(cidr: str) -> None:
    hosts = [str(h) for h in ipaddress.ip_network(cidr, strict=False).hosts()]

    def probe(ip):
        try:
            info = http_json(f"http://{ip}/shelly", timeout=0.8)
        except Exception:  # noqa: BLE001
            return None
        gen = int(info.get("gen", 1))
        name = info.get("name")
        if not name and gen >= 2:
            try:
                name = http_json(f"http://{ip}/rpc/Sys.GetConfig", timeout=2)["device"].get("name")
            except Exception:  # noqa: BLE001
                pass
        return ip, gen, info.get("model") or info.get("type"), name or info.get("id") or ip

    with ThreadPoolExecutor(64) as ex:
        found = [r for r in ex.map(probe, hosts) if r]
    print(f"# Shelly devices found: {len(found)}\n[devices]")
    for ip, gen, model, name in sorted(found, key=lambda r: str(r[3])):
        print(f'"{name}" = "{ip}"   # Gen{gen} {model}')


def run() -> None:
    con = db()
    Api.con = con
    start_gas(con)
    threading.Thread(target=poll_loop, args=(con,), daemon=True).start()
    print(f"[collector] {len(DEVICES)} devices, interval {INTERVAL}s, API :{PORT}, DB {DB}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Api).serve_forever()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "discover":
        discover(sys.argv[2] if len(sys.argv) > 2 else "192.168.178.0/24")
    elif cmd == "once":
        c = db()
        poll_once(c)
        print(json.dumps(list(STATE.values()), indent=2, ensure_ascii=False))
    elif cmd == "calibrate-gas":
        c = db()
        c.execute("INSERT INTO gas_calib VALUES (?,?)", (now(), float(sys.argv[2].replace(",", "."))))
        c.commit()
        print("gas meter calibrated to", sys.argv[2], "m3")
    else:
        run()
