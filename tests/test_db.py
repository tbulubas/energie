# Copyright (c) 2026 Taras Bulubas. All rights reserved.
# Use requires prior written permission; see LICENSE.

"""Schema version, backups, settings and the CSV import."""

import sqlite3

from energie import db


def test_schema_version(con):
    assert con.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_older_database_is_backed_up_first(temp_db):
    con = sqlite3.connect(temp_db)
    con.execute("PRAGMA user_version = 0")
    con.commit()
    con.close()
    db.init_db()
    backups = list(temp_db.parent.glob("test-backup-v0-*.db"))
    assert len(backups) == 1


def test_v1_prices_become_tariffs(temp_db):
    con = sqlite3.connect(temp_db)                      # a v1 database: prices in the settings
    con.execute("DROP TABLE tariffs")
    con.executemany("INSERT INTO settings VALUES(?,?)", [("electricity_eur_kwh", "0.352"), ("gas_eur_kwh", ""),
                                                         ("max_daily_gas", "12")])
    con.execute("PRAGMA user_version = 1")
    con.commit()
    con.close()
    db.init_db()
    con = db.get_db()
    assert db.tariffs(con) == [{"meter": "electricity", "valid_from": db.EARLIEST, "energy_eur_kwh": 0.352,
                                "base_eur_month": 0.0}]
    assert [r[0] for r in con.execute("SELECT key FROM settings")] == ["max_daily_gas"]
    con.close()
    assert len(list(temp_db.parent.glob("test-backup-v1-*.db"))) == 1


def test_settings_fall_back_to_environment_then_default(con, monkeypatch):
    monkeypatch.setattr(db, "COLLECTOR_URL", "http://env:8600")
    assert db.get_setting(con, "collector_url") == "http://env:8600"
    db.set_setting(con, "collector_url", "http://ui:8600")
    assert db.get_setting(con, "collector_url") == "http://ui:8600"
    assert db.get_float(con, "max_daily_gas") == 15.0


def test_relative_folder_is_taken_from_the_project(con):
    db.set_setting(con, "photos_dir", "meter-photos")
    assert db.folder(con, "photos_dir") == db.PROJECT_DIR / "meter-photos"


def test_load_csv(con, tmp_path):
    (tmp_path / "meter_readings.csv").write_text(
        "ts,meter,value,source_file,note\n2026-05-01 07:00,strom,1000.5,IMG_1.jpg,\n2026-05-01 07:01,gas,12.345,,\n",
        encoding="utf-8")
    (tmp_path / "shelly_readings.csv").write_text(
        "window_end,source,scope,floor,name,energy_wh\n2026-05-01 07:10,a.png;b.png,device,Basement,Fridge,700\n",
        encoding="utf-8")
    assert db.load_csv_dir(con, tmp_path) == {"meter_readings": 2, "shelly_periods": 1}
    assert {r["meter"] for r in con.execute("SELECT meter FROM meter_readings")} == {"electricity", "gas"}
    assert db.known_sources(con) == {"IMG_1.jpg", "a.png", "b.png"}
    assert db.load_csv_dir(con, tmp_path) == {"meter_readings": 0, "shelly_periods": 0}   # existing rows win
