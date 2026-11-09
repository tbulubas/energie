# Copyright (c) 2026 Taras Bulubas. All rights reserved.
# Use requires prior written permission; see LICENSE.

"""Shared fixtures. Every test runs against a throwaway database, never energie.db, and never
sees the user's photo and screenshot folders or the collector."""

import os

# The user's own settings point at the real database and folders: drop them before the package
# reads them on import
for _name in [n for n in os.environ if n.startswith("ENERGIE_")]:
    del os.environ[_name]

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from energie import db, gemma  # noqa: E402


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "PHOTOS_DIR", str(tmp_path / "photos"))
    monkeypatch.setattr(db, "SCREENSHOTS_DIR", str(tmp_path / "screenshots"))
    monkeypatch.setattr(db, "COLLECTOR_URL", None)
    (tmp_path / "photos").mkdir()
    (tmp_path / "screenshots").mkdir()
    # no Ollama in tests: the deterministic paths must work on their own
    monkeypatch.setattr(gemma, "available", lambda: False)
    db.init_db()
    return db.DB_PATH


@pytest.fixture
def con(temp_db):
    c = db.get_db()
    yield c
    c.close()


@pytest.fixture
def client():
    from energie.app import app

    with TestClient(app) as c:
        yield c


def add_readings(con, meter: str, rows: list[tuple[str, float]]):
    con.executemany("INSERT INTO meter_readings(ts,meter,value) VALUES(?,?,?)", [(t, meter, v) for t, v in rows])
    con.commit()
