# Copyright (c) 2026 Taras Bulubas. All rights reserved.
# Use requires prior written permission; see LICENSE.

"""Download new data from the collector (Raspberry Pi / Odroid, see collector/) into energie.db.

The collector runs around the clock and keeps cumulative counters; the app fetches what is new
since the last sync (settings 'sync_since', 'sync_gas_since'), so it is incremental and idempotent."""
import requests

from energie import db

PAGE = 20000
EPOCH = "2000-01-01 00:00:00"


def health(url: str) -> dict:
    try:
        return requests.get(f"{url}/api/health", timeout=4).json()
    except (requests.RequestException, ValueError) as e:
        return {"ok": False, "error": str(e)}


def sync(con, url: str) -> dict:
    since = db.get_setting(con, "sync_since") or EPOCH
    n_samples = 0
    while True:
        r = requests.get(f"{url}/api/samples", params={"since": since, "limit": PAGE}, timeout=30)
        r.raise_for_status()
        rows = r.json()["rows"]
        con.executemany("INSERT OR IGNORE INTO shelly_counters(ts,name,total_wh,apower_w) VALUES(?,?,?,?)", rows)
        n_samples += len(rows)
        if rows:
            since = rows[-1][0]
        if len(rows) < PAGE:
            break
    gas_since = db.get_setting(con, "sync_gas_since") or EPOCH
    g = requests.get(f"{url}/api/gas", params={"since": gas_since}, timeout=30).json()["rows"]
    con.executemany("INSERT OR IGNORE INTO meter_readings(ts,meter,value,method,note) "
                    "VALUES(substr(?,1,16),'gas',?,'pulse','collector')", g)
    con.commit()
    db.set_setting(con, "sync_since", since)
    if g:
        db.set_setting(con, "sync_gas_since", g[-1][0])
    return {"samples": n_samples, "gas": len(g), "since": since}
