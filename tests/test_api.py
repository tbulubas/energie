# Copyright (c) 2026 Taras Bulubas. All rights reserved.
# Use requires prior written permission; see LICENSE.

"""API behaviour against a throwaway database. All values are invented."""

import io
from datetime import datetime

from PIL import Image

from energie import db, gemma, importer


def test_empty_app_answers(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/vendor/chart.umd.js").status_code == 200        # bundled, no CDN
    icons = client.get("/static/manifest.json").json()["icons"]               # "Add to home screen"
    assert all(client.get(i["src"]).status_code == 200 for i in icons)
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
    assert s["gas_calorific_value"] == db.DEFAULT_SETTINGS["gas_calorific_value"] and "electricity_eur_kwh" not in s
    r = client.put("/api/settings", json={"gas_calorific_value": "11,2", "collector_url": "http://pi:8600/"})
    assert r.json()["gas_calorific_value"] == "11.2" and r.json()["collector_url"] == "http://pi:8600"
    assert client.put("/api/settings", json={"gas_correction_factor": "free"}).json()["detail"] == {"code": "bad_number", "info": "gas_correction_factor"}
    assert client.put("/api/settings", json={"colour": "red"}).json()["detail"]["code"] == "unknown_setting"
    # decimals on a counter: a whole number of drums, 0 allowed
    assert client.put("/api/settings", json={"decimals_electricity": "0"}).json()["decimals_electricity"] == "0"
    assert client.put("/api/settings", json={"decimals_gas": "1.5"}).json()["detail"] == {"code": "bad_number", "info": "decimals_gas"}
    # an empty value falls back to the default
    assert client.put("/api/settings", json={"gas_calorific_value": ""}).json()["gas_calorific_value"] == "11.35"


def test_tariffs_and_the_monthly_advance(client):
    put = lambda **t: client.put("/api/tariffs", json=t)
    assert client.get("/api/tariffs").json()["tariffs"] == []
    assert put(meter="water", valid_from="2026-01-01", energy_eur_kwh=0.3).json()["detail"] == {"code": "bad_meter"}
    assert put(meter="gas", valid_from="1.12.2026", energy_eur_kwh=0.3).json()["detail"] == {"code": "bad_date"}
    assert put(meter="gas", valid_from="2026-01-01", energy_eur_kwh=-1).json()["detail"]["code"] == "bad_number"
    put(meter="electricity", valid_from="2025-03-01", energy_eur_kwh=0.352, base_eur_month=11)
    r = put(meter="electricity", valid_from="2099-12-01", energy_eur_kwh=0.418, base_eur_month=15).json()
    assert [t["valid_from"] for t in r["tariffs"]] == ["2025-03-01", "2099-12-01"]

    # 30 days at 10 kWh a day: 3650 kWh a year
    for d, v in (("2026-05-01 07:00", 1000.0), ("2026-05-31 07:00", 1300.0)):
        client.put("/api/readings", json={"ts": d, "meter": "electricity", "value": v})
    o = client.get("/api/overview").json()["outlook"]
    assert "gas" not in o
    el = o["electricity"]
    assert round(el["kwh_year"]) == 3650 and round(el["days"]) == 30
    assert round(el["now"]["eur_month"], 2) == round((3650 * 0.352 + 132) / 12, 2)
    assert el["next"]["valid_from"] == "2099-12-01" and round(el["next"]["eur_year"], 1) == round(3650 * 0.418 + 180, 1)

    client.delete("/api/tariffs", params={"meter": "electricity", "valid_from": "2099-12-01"})
    assert client.get("/api/overview").json()["outlook"]["electricity"]["next"] is None


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


def jpeg(taken: str | None = None) -> bytes:
    """A tiny JPEG, with EXIF DateTimeOriginal ('2026:05:02 07:15:00') if given."""
    exif = Image.Exif()
    if taken:
        exif.get_ifd(0x8769)[36867] = taken
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def test_upload_names_from_a_phone(tmp_path):
    folder = tmp_path / "up"
    folder.mkdir()
    now = datetime(2026, 5, 2, 8, 30, 0)
    # a camera picture sent as image.jpg: the time it was taken, else now, becomes the name
    assert importer.upload_name(folder, "image.jpg", jpeg("2026:05:02 07:15:00"), now) == "IMG_20260502_071500.jpg"
    assert importer.upload_name(folder, "1746170000123.JPG", jpeg(), now) == "IMG_20260502_083000.jpg"
    # a name with a time stays; another file under the same name gets a suffix, the same file not
    (folder / "IMG_20260502_071500.jpg").write_bytes(b"first")
    assert importer.upload_name(folder, "IMG_20260502_071500.jpg", b"first", now) == "IMG_20260502_071500.jpg"
    assert importer.upload_name(folder, "IMG_20260502_071500.jpg", b"second", now) == "IMG_20260502_071500-2.jpg"
    assert importer.file_ts(folder / "IMG_20260502_071500-2.jpg") == datetime(2026, 5, 2, 7, 15)


def test_photo_from_the_phone_is_read_at_once(client, tmp_path, monkeypatch):
    client.put("/api/readings", json={"ts": "2026-05-01 07:00", "meter": "gas", "value": 100.0})
    monkeypatch.setattr(gemma, "available", lambda: True)
    monkeypatch.setattr(gemma, "read_meter", lambda data, **kw: {
        "meter": "gas", "black_digits": "00101", "red_digits": "250", "rolling": False, "value": 101.25})
    r = client.post("/api/upload", data={"kind": "meter", "read": "true"},
                    files={"file": ("image.jpg", jpeg("2026:05:02 07:15:00"), "image/jpeg")}).json()
    assert r["saved"] == "IMG_20260502_071500.jpg"
    assert r["result"] | {"issues": []} == {"source": "IMG_20260502_071500.jpg", "kind": "meter", "status": "accepted",
                                           "issues": [], "meter": "gas", "value": 101.25, "ts": "2026-05-02 07:15"}
    assert client.get("/api/readings?meter=gas").json()[0]["value"] == 101.25

    # without Gemma the photo is kept and waits for the Import tab
    monkeypatch.setattr(gemma, "available", lambda: False)
    r = client.post("/api/upload", data={"kind": "meter", "read": "true"},
                    files={"file": ("image.jpg", jpeg("2026:05:03 07:15:00"), "image/jpeg")}).json()
    assert r == {"saved": "IMG_20260503_071500.jpg", "result": None}
    assert client.get("/api/status").json()["new_meter"] == 1
