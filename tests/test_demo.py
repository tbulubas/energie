# Copyright (c) 2026 Taras Bulubas. All rights reserved.
# Use requires prior written permission; see LICENSE.

"""The demo: an invented house in a fresh folder, consistent with the app's own checks."""

from datetime import date

from fastapi.testclient import TestClient

from energie import db, demo, importer


def test_demo_builds_a_consistent_example(tmp_path):
    path = demo.build(tmp_path, today=date(2026, 11, 9))
    assert path == tmp_path / "energie-demo.db" and db.DB_PATH == path
    from energie.app import app
    with TestClient(app) as client:
        ov = client.get("/api/overview").json()
        assert 8 < ov["kpi"]["el_per_day"] < 16 and 0.3 < ov["kpi"]["coverage"] < 0.8
        assert {i["code"] for i in ov["insights"]} >= {"coverage", "steady", "gas_trend", "base_load"}
        assert len(ov["coverage"]) == demo.SHELLY_DAYS
        dev = client.get("/api/devices").json()
        assert {d["name"] for d in dev["stats"]} == set(demo.DEVICES) and dev["hourly"]
        pend = client.get("/api/pending").json()
        assert len(pend) == 1 and pend[0]["issues"][0]["code"] == "increase_too_large"
        assert client.get("/api/image", params={"name": pend[0]["files"][0]}).status_code == 200
        assert client.get("/api/status").json()["new_meter"] == 1
    con = db.get_db()
    # every Shelly window of the demo passes the importer's own sum checks
    for (w,) in con.execute("SELECT DISTINCT window_end FROM shelly_periods"):
        rows = con.execute("SELECT * FROM shelly_periods WHERE window_end=?", (w,)).fetchall()
        cards = [{"title": "Total", "total_wh": next(r["energy_wh"] for r in rows if r["scope"] == "total"), "devices": {}}]
        for f in {r["floor"] for r in rows if r["scope"] == "floor"}:
            cards.append({"title": f, "total_wh": next(r["energy_wh"] for r in rows if r["scope"] == "floor" and r["name"] == f),
                          "devices": {r["name"]: r["energy_wh"] for r in rows if r["scope"] == "device" and r["floor"] == f}})
        assert importer.check_shelly(cards) == []
    con.close()
