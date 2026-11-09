# Copyright (c) 2026 Taras Bulubas. All rights reserved.
# Use requires prior written permission; see LICENSE.

"""Checks, grouping and review of Gemma's results. Gemma itself is replaced by fixed answers."""

import json
from datetime import datetime
from pathlib import Path

from energie import db, gemma, importer
from conftest import add_readings


def codes(issues):
    return [i["code"] for i in issues]


def test_meter_checks(con):
    add_readings(con, "electricity", [("2026-05-01 07:00", 1000.0), ("2026-05-03 07:00", 1020.0)])
    at = datetime(2026, 5, 2, 7, 0)
    assert importer.check_meter(con, "electricity", at, 1010.0) == []
    assert codes(importer.check_meter(con, "electricity", at, 999.0)) == ["counter_decreases"]
    assert codes(importer.check_meter(con, "electricity", at, 1100.0)) == ["increase_too_large", "counter_decreases"]
    assert codes(importer.check_meter(con, "gas", at, 5.0)) == ["no_reference"]


def test_shelly_checks():
    good = [{"title": "Gesamtenergie", "total_wh": 3000, "devices": {}},
            {"title": "Basement", "total_wh": 1000, "devices": {"Fridge": 700, "Washer": 300}},
            {"title": "Attic", "total_wh": 2000, "devices": {"Office": 2000}}]
    assert importer.check_shelly(good) == []
    bad = [{"title": "Total energy", "total_wh": 5000, "devices": {}},
           {"title": "Basement", "total_wh": 1000, "devices": {"Fridge": 300}},
           {"title": "Attic", "total_wh": None, "devices": {}}]
    assert codes(importer.check_shelly(bad)) == ["devices_mismatch", "missing_floor_total"]


def test_names_are_matched_to_known_ones():
    known = ["Office-2nd-Floor", "Fridge-Kitchen", "Fridge-Cellar"]
    assert importer.normalize("Office-2nd-Fl…", known) == "Office-2nd-Floor"
    assert importer.normalize("Fridge-Kitche...", known) == "Fridge-Kitchen"
    assert importer.normalize("Fridge-", known) == "Fridge-"            # ambiguous prefix: kept as read
    assert importer.normalize("New plug", known) == "New plug"


def test_time_from_file_name_and_grouping(tmp_path):
    names = ["Screenshot_20260502-071830.png", "Screenshot_20260502-071846.png", "Screenshot_20260503-072821.png"]
    files = []
    for n in names:
        (tmp_path / n).write_bytes(b"x")
        files.append(tmp_path / n)
    assert importer.file_ts(files[0]) == datetime(2026, 5, 2, 7, 18, 30)
    assert [[p.name for p in g] for g in importer.group_screenshots(files)] == [names[:2], names[2:]]


def _photo(con, name: str) -> Path:
    p = db.folder(con, "photos_dir") / name
    p.write_bytes(b"fake image")
    return p


def test_plausible_reading_goes_straight_in(con, monkeypatch):
    add_readings(con, "electricity", [("2026-05-01 07:00", 1000.0)])
    monkeypatch.setattr(gemma, "read_meter", lambda data, two_pass=True: {
        "meter": "electricity", "black_digits": "001009", "red_digits": "5", "rolling": False, "value": 1009.5})
    todo = importer.jobs(con)
    assert todo == [] and importer.new_files(con)["meter"] == []
    _photo(con, "IMG_20260502_070000123.jpg")
    kind, paths = importer.jobs(con)[0]
    r = importer.run_job(con, kind, paths)
    assert r["status"] == "accepted"
    row = con.execute("SELECT * FROM meter_readings WHERE ts='2026-05-02 07:00'").fetchone()
    assert row["value"] == 1009.5 and row["method"] == "gemma"
    assert importer.new_files(con)["meter"] == []                     # known now


def test_misread_waits_for_review_and_can_be_corrected(con, monkeypatch):
    add_readings(con, "electricity", [("2026-05-01 07:00", 1000.0)])
    monkeypatch.setattr(gemma, "read_meter", lambda data, two_pass=True: {
        "meter": "electricity", "black_digits": "101009", "red_digits": "5", "rolling": True, "value": 101009.5})
    p = _photo(con, "IMG_20260502_070000123.jpg")
    r = importer.run_job(con, "meter", [p])
    assert r["status"] == "pending" and codes(r["issues"]) == ["increase_too_large"]
    pid = con.execute("SELECT id FROM pending_reads").fetchone()[0]
    importer.accept(con, pid, {"value": 1009.5})
    row = con.execute("SELECT * FROM meter_readings WHERE ts='2026-05-02 07:00'").fetchone()
    assert row["value"] == 1009.5 and row["method"] == "manual"


def test_auto_accept_off_keeps_everything_for_review(con, monkeypatch):
    db.set_setting(con, "auto_accept", "0")
    add_readings(con, "gas", [("2026-05-01 07:00", 100.0)])
    monkeypatch.setattr(gemma, "read_meter", lambda data, two_pass=True: {
        "meter": "gas", "black_digits": "00100", "red_digits": "900", "rolling": False, "value": 100.9})
    r = importer.run_job(con, "meter", [_photo(con, "IMG_20260502_070000123.jpg")])
    assert r["status"] == "pending" and r["issues"] == []


def test_screenshot_group_is_stored_per_scope(con, monkeypatch):
    shots = db.folder(con, "screenshots_dir")
    for n in ("Screenshot_20260502-071830.png", "Screenshot_20260502-071846.png"):
        (shots / n).write_bytes(b"x")
    answers = iter([
        {"cards": [{"title": "Gesamtenergie", "total_wh": 1500, "devices": {}},
                   {"title": "Basement", "total_wh": 1000, "devices": {"Fridge": 700}}]},
        {"cards": [{"title": "Basement", "total_wh": None, "devices": {"Washer": 300}},
                   {"title": "Attic", "total_wh": 500, "devices": {"Office": 500}}]}])
    monkeypatch.setattr(gemma, "read_shelly_screenshot", lambda data: next(answers))
    kind, paths = importer.jobs(con)[0]
    assert kind == "shelly" and len(paths) == 2
    assert importer.run_job(con, kind, paths)["status"] == "accepted"
    rows = {(r["scope"], r["name"]): r["energy_wh"] for r in con.execute("SELECT * FROM shelly_periods")}
    assert rows == {("total", "total"): 1500, ("floor", "Basement"): 1000, ("device", "Fridge"): 700,
                    ("device", "Washer"): 300, ("floor", "Attic"): 500, ("device", "Office"): 500}
    stored = con.execute("SELECT issues FROM pending_reads").fetchone()[0]
    assert json.loads(stored) == []


def test_a_failing_read_is_reported_not_raised(con, monkeypatch):
    def boom(data, two_pass=True):
        raise RuntimeError("model not found")
    monkeypatch.setattr(gemma, "read_meter", boom)
    r = importer.run_job(con, "meter", [_photo(con, "IMG_20260502_070000123.jpg")])
    assert r["status"] == "error" and r["issues"][0]["code"] == "read_failed"
