# Copyright (c) 2026 Taras Bulubas. All rights reserved.
# Use requires prior written permission; see LICENSE.

"""SQLite database for home energy data. All data stays local in energie.db."""
import csv
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

# project root (src/energie/db.py -> ../..); the package is installed in editable mode
PROJECT_DIR = Path(__file__).resolve().parents[2]

# The database can live outside this folder: set ENERGIE_DB=C:\Energie\energie.db
DB_PATH = Path(os.environ.get("ENERGIE_DB", PROJECT_DIR / "energie.db"))

# Default folders with meter photos and Shelly app screenshots, until chosen in the UI (settings)
PHOTOS_DIR = os.environ.get("ENERGIE_PHOTOS")
SCREENSHOTS_DIR = os.environ.get("ENERGIE_SCREENSHOTS")
# Address of the collector (Raspberry Pi / Odroid), until set in the UI
COLLECTOR_URL = os.environ.get("ENERGIE_COLLECTOR_URL")

# PRAGMA user_version of the current schema; init_db() migrates older databases
SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meter_readings (
    ts          TEXT NOT NULL,              -- local time 'YYYY-MM-DD HH:MM'
    meter       TEXT NOT NULL,              -- 'electricity' | 'gas'
    value       REAL NOT NULL,              -- register reading (kWh / m3)
    source_file TEXT,
    note        TEXT,
    method      TEXT NOT NULL DEFAULT 'manual',   -- manual | gemma | pulse | csv
    PRIMARY KEY (ts, meter)
);

-- 24 h totals read from Shelly app screenshots ("History - last 24 hours")
CREATE TABLE IF NOT EXISTS shelly_periods (
    window_end  TEXT NOT NULL,              -- end of the 24 h window = time of the screenshot
    scope       TEXT NOT NULL,              -- total | floor | device
    floor       TEXT,
    name        TEXT NOT NULL,
    energy_wh   REAL NOT NULL,
    source      TEXT,                       -- screenshot file names, ';'-separated
    PRIMARY KEY (window_end, scope, name)
);

-- cumulative energy counters from the collector
CREATE TABLE IF NOT EXISTS shelly_counters (
    ts          TEXT NOT NULL,              -- 'YYYY-MM-DD HH:MM:SS'
    name        TEXT NOT NULL,
    total_wh    REAL NOT NULL,
    apower_w    REAL,
    PRIMARY KEY (ts, name)
);
CREATE INDEX IF NOT EXISTS idx_counters_name_ts ON shelly_counters(name, ts);

-- Gemma results waiting for review
CREATE TABLE IF NOT EXISTS pending_reads (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,              -- meter | shelly
    source_file TEXT NOT NULL UNIQUE,       -- file name(s), ';'-separated
    ts          TEXT,
    payload     TEXT NOT NULL,              -- JSON read by Gemma
    issues      TEXT NOT NULL DEFAULT '[]', -- JSON list of {"code": ..., ...}
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending | accepted | rejected
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- prices from the supplier's letters and bills (gross, incl. VAT); a row holds from valid_from
-- until the next row of the same meter
CREATE TABLE IF NOT EXISTS tariffs (
    meter          TEXT NOT NULL,           -- electricity | gas
    valid_from     TEXT NOT NULL,           -- 'YYYY-MM-DD'
    energy_eur_kwh REAL NOT NULL,           -- price per kWh (gas: per kWh after the m3 conversion)
    base_eur_month REAL NOT NULL DEFAULT 0, -- standing charge per month
    PRIMARY KEY (meter, valid_from)
);
"""

METERS = ("electricity", "gas")
# older names in hand-made CSV files
METER_ALIASES = {"strom": "electricity", "elektrina": "electricity", "power": "electricity", "plyn": "gas"}

# Price per kWh until the user enters the prices from the bills (Settings tab, table tariffs)
DEFAULT_PRICE_EUR_KWH = {"electricity": 0.32, "gas": 0.11}
# the date of a price that held before any known change (prices moved from the settings in v2)
EARLIEST = "2000-01-01"

# Placeholders until the user enters the values from the bills (Settings tab)
DEFAULT_SETTINGS = {
    "gas_correction_factor": "0.955",      # Zustandszahl on the gas bill
    "gas_calorific_value": "11.35",        # Brennwert in kWh/m3 on the gas bill
    "max_daily_electricity": "60",         # plausibility limits for readings (per day)
    "max_daily_gas": "15",
    "decimals_electricity": "1",           # digits after the decimal point on the counter (red drums)
    "decimals_gas": "3",
    "auto_accept": "1",                   # readings that pass all checks go straight in
    "photos_dir": "",
    "screenshots_dir": "",
    "collector_url": "",
    "sync_minutes": "5",                   # how often the app pulls new data from the collector
}


def get_db() -> sqlite3.Connection:
    # check_same_thread=False: the import runs in a worker thread
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = get_db()
    version = con.execute("PRAGMA user_version").fetchone()[0]
    existing = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meter_readings'").fetchone()
    if existing and version < SCHEMA_VERSION:
        backup_db(con, version)
    con.executescript(SCHEMA)
    if existing and version < 2:
        _migrate_v1_to_v2(con)
    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    con.commit()
    con.close()


def _migrate_v1_to_v2(con):
    """v2 keeps prices with the date they start (table tariffs) instead of one price per meter in
    the settings: a price the user had entered becomes a tariff valid from the start."""
    for meter in METERS:
        row = con.execute("SELECT value FROM settings WHERE key=?", (f"{meter}_eur_kwh",)).fetchone()
        if row and row["value"] not in (None, ""):
            con.execute("INSERT OR IGNORE INTO tariffs(meter, valid_from, energy_eur_kwh, base_eur_month) "
                        "VALUES(?,?,?,0)", (meter, EARLIEST, float(row["value"])))
        con.execute("DELETE FROM settings WHERE key=?", (f"{meter}_eur_kwh",))


def backup_db(con, version: int) -> Path:
    """Copy the database next to itself before a migration, e.g. energie-backup-v0-20261109-171200.db."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = DB_PATH.with_name(f"{DB_PATH.stem}-backup-v{version}-{stamp}.db")
    dst = sqlite3.connect(target)
    con.backup(dst)
    dst.close()
    print(f"[db] backup before migration: {target}")
    return target


# ---------- settings ----------

def get_setting(con, key: str) -> str:
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row and row["value"] not in (None, ""):
        return row["value"]
    env = {"photos_dir": PHOTOS_DIR, "screenshots_dir": SCREENSHOTS_DIR, "collector_url": COLLECTOR_URL}
    return env.get(key) or DEFAULT_SETTINGS.get(key, "")


def get_float(con, key: str) -> float:
    return float(get_setting(con, key))


def set_setting(con, key: str, value: str | None):
    con.execute("""INSERT INTO settings(key, value) VALUES (?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (key, value))
    con.commit()


def all_settings(con) -> dict[str, str]:
    return {k: get_setting(con, k) for k in DEFAULT_SETTINGS}


# ---------- tariffs ----------

def tariffs(con, meter: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM tariffs", ()
    if meter:
        sql, args = sql + " WHERE meter=?", (meter,)
    return [dict(r) for r in con.execute(sql + " ORDER BY meter, valid_from", args)]


def put_tariff(con, meter: str, valid_from: str, energy_eur_kwh: float, base_eur_month: float):
    con.execute("""INSERT INTO tariffs(meter, valid_from, energy_eur_kwh, base_eur_month) VALUES(?,?,?,?)
                   ON CONFLICT(meter, valid_from) DO UPDATE SET energy_eur_kwh=excluded.energy_eur_kwh,
                   base_eur_month=excluded.base_eur_month""", (meter, valid_from, energy_eur_kwh, base_eur_month))
    con.commit()


def delete_tariff(con, meter: str, valid_from: str):
    con.execute("DELETE FROM tariffs WHERE meter=? AND valid_from=?", (meter, valid_from))
    con.commit()


def folder(con, key: str) -> Path | None:
    """photos_dir / screenshots_dir as a Path; relative paths are taken from the project folder."""
    v = get_setting(con, key)
    if not v:
        return None
    p = Path(v)
    return p if p.is_absolute() else PROJECT_DIR / p


# ---------- CSV import of hand-checked data ----------

def load_csv_dir(con, path: Path) -> dict[str, int]:
    """Import meter_readings.csv and shelly_readings.csv (hand-checked data). Existing rows win."""
    n = {}
    p = path / "meter_readings.csv"
    if p.exists():
        rows = []
        for r in csv.DictReader(p.open(encoding="utf-8")):
            meter = METER_ALIASES.get(r["meter"].strip().lower(), r["meter"].strip().lower())
            rows.append((r["ts"], meter, float(r["value"]), r.get("source_file") or None, r.get("note") or ""))
        cur = con.executemany("INSERT OR IGNORE INTO meter_readings(ts,meter,value,source_file,note,method) "
                              "VALUES(?,?,?,?,?,'csv')", rows)
        n["meter_readings"] = cur.rowcount
    p = path / "shelly_readings.csv"
    if p.exists():
        rows = [(r["window_end"], r["scope"], r.get("floor") or "", r["name"], float(r["energy_wh"]),
                 r.get("source") or None) for r in csv.DictReader(p.open(encoding="utf-8"))]
        cur = con.executemany("INSERT OR IGNORE INTO shelly_periods VALUES(?,?,?,?,?,?)", rows)
        n["shelly_periods"] = cur.rowcount
    con.commit()
    return n


def known_sources(con) -> set[str]:
    """File names already read (in the database or waiting for review)."""
    out: set[str] = set()
    for (src,) in con.execute("SELECT source_file FROM meter_readings WHERE source_file IS NOT NULL"):
        out.add(src)
    for q in ("SELECT source_file FROM pending_reads", "SELECT DISTINCT source FROM shelly_periods"):
        for (src,) in con.execute(q):
            if src:
                out.update(src.split(";"))
    return out


def issues_json(issues: list[dict]) -> str:
    return json.dumps(issues, ensure_ascii=False)
