# Copyright (c) 2026 Taras Bulubas. All rights reserved.
# Use requires prior written permission; see LICENSE.

"""The collector (collector/collector.py) against invented Shelly devices on localhost, and the
app's sync from its API."""

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from energie import db, sync

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def collector(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("collector", ROOT / "collector" / "collector.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "DB", tmp_path / "collector.db")
    return mod


def serve(handler) -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"127.0.0.1:{srv.server_address[1]}"


def shelly(answers: dict):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = answers.get(self.path.split("?")[0])
            data = json.dumps(body or {}).encode()
            self.send_response(200 if body else 404)
            self.end_headers()
            self.wfile.write(data)
    return H


GEN2_PLUG = {"/shelly": {"gen": 2, "model": "SNPL-00112EU"},
             "/rpc/Shelly.GetStatus": {"switch:0": {"apower": 35.2, "aenergy": {"total": 1234.5}}, "sys": {}}}
GEN1_PLUG = {"/shelly": {"type": "SHPLG-S"}, "/status": {"meters": [{"power": 12.0, "total": 60000}]}}
PRO_3EM = {"/shelly": {"gen": 2, "model": "SPEM-003CEBEU"},
           "/rpc/Shelly.GetStatus": {"em:0": {"total_act_power": 420.0}, "emdata:0": {"total_act": 9876543.0}}}


def test_reads_every_generation_and_survives_an_offline_device(collector):
    servers = {n: serve(shelly(a)) for n, a in (("Plug2", GEN2_PLUG), ("Plug1", GEN1_PLUG), ("House", PRO_3EM))}
    collector.DEVICES = {n: host for n, (_, host) in servers.items()} | {"Offline": "127.0.0.1:9"}
    collector.STATE = {n: {"name": n, "ip": ip} for n, ip in collector.DEVICES.items()}
    con = collector.db()
    collector.poll_once(con)
    st = collector.STATE
    assert (st["Plug2"]["gen"], st["Plug2"]["total_wh"], st["Plug2"]["apower_w"]) == (2, 1234.5, 35.2)
    assert (st["Plug1"]["gen"], st["Plug1"]["total_wh"]) == (1, 1000.0)          # watt-minutes -> Wh
    assert st["House"]["total_wh"] == 9876543.0 and st["House"]["apower_w"] == 420.0
    assert st["Offline"]["error"] and "gen" not in st["Offline"]
    assert con.execute("SELECT COUNT(*) FROM sample").fetchone()[0] == 3
    for srv, _ in servers.values():
        srv.shutdown()


def test_gas_pulses_after_calibration(collector):
    con = collector.db()
    collector.GAS = {"m3_per_pulse": 0.1}
    assert collector.gas_total(con) == (None, [])
    con.execute("INSERT INTO gas_calib VALUES ('2026-05-01 07:00:00', 1000.0)")
    con.executemany("INSERT INTO gas_pulse VALUES (?)", [("2026-05-01 06:00:00",), ("2026-05-01 09:00:00",),
                                                          ("2026-05-01 12:00:00",)])
    total, series = collector.gas_total(con)
    assert total == 1000.2 and series == [("2026-05-01 07:00:00", 1000.0), ("2026-05-01 09:00:00", 1000.1),
                                          ("2026-05-01 12:00:00", 1000.2)]


def test_app_syncs_incrementally(collector, con):
    c = collector.db()
    c.executemany("INSERT INTO sample VALUES (?,?,?,?)", [
        ("2026-05-01 10:00:00", "Fridge", 5000, 30), ("2026-05-01 10:01:00", "Fridge", 5001, 30)])
    c.execute("INSERT INTO gas_calib VALUES ('2026-05-01 07:00:00', 1000.0)")
    c.execute("INSERT INTO gas_pulse VALUES ('2026-05-01 09:00:00')")
    c.commit()
    collector.Api.con = c
    collector.GAS = {"m3_per_pulse": 0.1}
    srv, host = serve(collector.Api)
    url = f"http://{host}"
    assert sync.health(url)["ok"] is True
    assert sync.sync(con, url) == {"samples": 2, "gas": 2, "since": "2026-05-01 10:01:00"}
    c.execute("INSERT INTO sample VALUES ('2026-05-01 10:02:00', 'Fridge', 5002, 30)")
    c.commit()
    assert sync.sync(con, url)["samples"] == 1                              # only what is new
    assert con.execute("SELECT COUNT(*) FROM shelly_counters").fetchone()[0] == 3
    gas = [tuple(r) for r in con.execute("SELECT ts, value, method FROM meter_readings ORDER BY ts")]
    assert gas == [("2026-05-01 07:00", 1000.0, "pulse"), ("2026-05-01 09:00", 1000.1, "pulse")]
    assert db.get_setting(con, "sync_since") == "2026-05-01 10:02:00"
    srv.shutdown()
