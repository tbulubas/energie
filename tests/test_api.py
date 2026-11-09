# Copyright (c) 2026 Taras Bulubas. All rights reserved.
# Use requires prior written permission; see LICENSE.

"""API behaviour against a throwaway database. All values are invented."""

from energie import db


def test_empty_app_answers(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/vendor/chart.umd.js").status_code == 200        # bundled, no CDN
    s = client.get("/api/status").json()
    assert s["gemma"] is False and s["pending"] == 0 and s["new_meter"] == 0
    ov = client.get("/api/overview").json()
    assert ov["kpi"]["el_per_day"] is None and ov["insights"] == [] and ov["electricity"] == []
    assert client.get("/api/devices").json() == {"stats": [], "hourly": [], "base": []}


def test_readings_crud_with_warnings(client):
    put = lambda ts, v: client.put("/api/readings", json={"ts": ts, "meter": "electricity", "value": v})
    assert put("2026-05-01 07:00", 1000).json() == {"ok": True, "warnings": []}
    assert put("2026-05-02 07:00", 1010).json()["warnings"] == []
    w = put("2026-05-03 07:00", 900).json()["warnings"]
    assert [x["code"] for x in w] == ["counter_decreases"]
    assert len(client.get("/api/readings").json()) == 3
    client.delete("/api/readings", params={"ts": "2026-05-03 07:00", "meter": "electricity"})
    assert [r["ts"] for r in client.get("/api/readings?meter=electricity").json()] == ["2026-05-02 07:00", "2026-05-01 07:00"]
    ov = client.get("/api/overview").json()
    assert round(ov["kpi"]["el_per_day"], 3) == 10.0


def test_errors_are_codes(client):
    bad = client.put("/api/readings", json={"ts": "yesterday", "meter": "electricity", "value": 1})
    assert bad.status_code == 400 and bad.json()["detail"] == {"code": "bad_time"}
    assert client.put("/api/readings", json={"ts": "2026-05-01 07:00", "meter": "water", "value": 1}).json()["detail"] == {"code": "bad_meter"}
    assert client.post("/api/import/scan").json()["detail"] == {"code": "ollama_missing"}
    assert client.post("/api/commentary").json()["detail"] == {"code": "ollama_missing"}
    assert client.post("/api/sync").json()["detail"] == {"code": "no_collector_url"}
    assert client.get("/api/collector").json() == {"ok": False, "error": "no_url"}


def test_settings(client):
    s = client.get("/api/settings").json()
    assert s["electricity_eur_kwh"] == db.DEFAULT_SETTINGS["electricity_eur_kwh"]
    r = client.put("/api/settings", json={"electricity_eur_kwh": "0,35", "collector_url": "http://pi:8600/"})
    assert r.json()["electricity_eur_kwh"] == "0.35" and r.json()["collector_url"] == "http://pi:8600"
    assert client.put("/api/settings", json={"gas_eur_kwh": "free"}).json()["detail"] == {"code": "bad_number", "info": "gas_eur_kwh"}
    assert client.put("/api/settings", json={"colour": "red"}).json()["detail"]["code"] == "unknown_setting"
    # an empty value falls back to the default
    assert client.put("/api/settings", json={"electricity_eur_kwh": ""}).json()["electricity_eur_kwh"] == "0.32"


def test_images_only_from_the_folders(client, tmp_path):
    (tmp_path / "photos" / "IMG_20260502_070000123.jpg").write_bytes(b"jpeg")
    (tmp_path / "secret.txt").write_text("no")
    assert client.get("/api/image", params={"name": "IMG_20260502_070000123.jpg"}).status_code == 200
    assert client.get("/api/image", params={"name": "../secret.txt"}).status_code == 404


def test_upload_and_review(client, tmp_path):
    r = client.post("/api/upload", data={"kind": "meter"}, files={"file": ("IMG_20260502_070000123.jpg", b"x", "image/jpeg")})
    assert r.json() == {"saved": "IMG_20260502_070000123.jpg"} and (tmp_path / "photos" / "IMG_20260502_070000123.jpg").exists()
    assert client.post("/api/upload", data={"kind": "meter"}, files={"file": ("notes.txt", b"x")}).json()["detail"] == {"code": "bad_file"}
    assert client.get("/api/status").json()["new_meter"] == 1
    con = db.get_db()
    con.execute("INSERT INTO pending_reads(kind, source_file, ts, payload, issues) VALUES('meter', ?, '2026-05-02 07:00', ?, ?)",
                ("IMG_20260502_070000123.jpg", '{"meter": "gas", "value": 9.0}', '[{"code": "no_reference"}]'))
    con.commit()
    con.close()
    p = client.get("/api/pending").json()
    assert p[0]["issues"] == [{"code": "no_reference"}] and p[0]["files"] == ["IMG_20260502_070000123.jpg"]
    client.post(f"/api/pending/{p[0]['id']}/accept", json={"override": {"value": 9.5}})
    assert client.get("/api/readings").json()[0]["value"] == 9.5
    assert client.get("/api/pending").json() == [] and client.get("/api/status").json()["new_meter"] == 0
