# Copyright (c) 2026 Taras Bulubas. All rights reserved.
# Use requires prior written permission; see LICENSE.

"""Local home energy web app. Run: python -m energie -> http://localhost:8510"""
import json
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from energie import analysis as A
from energie import db, gemma, importer, sync

JOB = {"running": False, "done": 0, "total": 0, "log": []}
JOB_LOCK = threading.Lock()


def _sync_in_background():
    con = db.get_db()
    try:
        url = db.get_setting(con, "collector_url")
        if url:
            print("[sync]", sync.sync(con, url))
    except Exception as e:  # noqa: BLE001 - the collector may be off
        print("[sync] collector not reachable:", e)
    finally:
        con.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # created on startup, not on import, so tests can point db.DB_PATH elsewhere first
    db.init_db()
    threading.Thread(target=_sync_in_background, daemon=True).start()
    yield


app = FastAPI(title="Energie", lifespan=lifespan)

STATIC = Path(__file__).parent / "static"
# the page's own files and the bundled libraries (Chart.js): nothing is loaded from the internet
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _records(df) -> list[dict]:
    if df is None or len(df) == 0:
        return []
    return json.loads(df.to_json(orient="records", date_format="iso"))


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    con = db.get_db()
    try:
        pending = con.execute("SELECT COUNT(*) FROM pending_reads WHERE status='pending'").fetchone()[0]
        nf = importer.new_files(con)
        last = con.execute("SELECT MAX(ts) FROM shelly_counters").fetchone()[0]
        url = db.get_setting(con, "collector_url")
    finally:
        con.close()
    return {"gemma": gemma.available(), "model": gemma.MODEL, "pending": pending,
            "new_meter": len(nf["meter"]), "new_shelly": len(nf["shelly"]),
            "collector_url": url, "collector_last": last}


# ---------- Overview ----------

@app.get("/api/overview")
def overview():
    con = db.get_db()
    try:
        p = A.Prices.from_db(con)
        sm = A.summary(con, p)
        cov = A.coverage(con)
        cov = cov[cov["full"]]
        el_iv = A.meter_intervals(con, "electricity")
        gas = A.gas_intervals(con, p)
        found = A.insights(con, p)
    finally:
        con.close()
    el, gs = sm["electricity"], sm["gas"]
    return {
        "kpi": {"el_per_day": el.get("per_day"), "el_per_year": el.get("per_year"),
                "el_eur_year": sm["electricity_eur_year"], "coverage": sm["coverage_mean"],
                "unmetered": sm["unmetered_mean_kwh"], "gas_m3_day": gs.get("per_day"),
                "gas_kwh_day": gs.get("kwh_per_day"), "days": el.get("days"),
                "from": f"{el['from']:%Y-%m-%d}" if el else None, "to": f"{el['to']:%Y-%m-%d}" if el else None},
        "coverage": [{"day": f"{r['day']:%Y-%m-%d}", "shelly": r["shelly_kwh"], "rest": r["unmetered_kwh"]}
                     for _, r in cov.iterrows()],
        "electricity": [{"day": f"{r['t1']:%Y-%m-%d}", "kwh": r["per_24h"], "hours": r["hours"]}
                        for _, r in el_iv.iterrows()],
        "gas": [{"day": f"{r['t1']:%Y-%m-%d}", "m3": r["per_24h"], "kwh": r["kwh_per_24h"], "hours": r["hours"]}
                for _, r in gas.iterrows()],
        "insights": found,
        "gas_kwh_per_m3": p.gas_kwh_per_m3,
    }


@app.post("/api/commentary")
def commentary(lang: str = "en"):
    if not gemma.available():
        raise HTTPException(400, {"code": "ollama_missing"})
    con = db.get_db()
    try:
        p = A.Prices.from_db(con)
        facts = {"summary": A.summary(con, p), "findings": A.insights(con, p),
                 "devices": _records(A.device_stats(con, p)),
                 "prices": {"electricity_eur_kwh": p.electricity_eur_kwh, "gas_eur_kwh": p.gas_eur_kwh}}
    finally:
        con.close()
    return {"text": gemma.commentary(facts, lang)}


# ---------- Devices ----------

@app.get("/api/devices")
def devices(days: int = 7):
    con = db.get_db()
    try:
        p = A.Prices.from_db(con)
        stats = A.device_stats(con, p)
        hourly = A.counter_hourly(con, days)
        base = A.base_load(con, days)
    finally:
        con.close()
    return {"stats": _records(stats), "hourly": _records(hourly), "base": _records(base)}


# ---------- Readings ----------

@app.get("/api/readings")
def readings(meter: str | None = None, limit: int = 500):
    sql, params = "SELECT * FROM meter_readings", []
    if meter:
        sql += " WHERE meter=?"
        params.append(meter)
    con = db.get_db()
    try:
        return [dict(r) for r in con.execute(sql + " ORDER BY ts DESC LIMIT ?", (*params, limit))]
    finally:
        con.close()


class ReadingIn(BaseModel):
    ts: str
    meter: str
    value: float
    note: str = ""


@app.put("/api/readings")
def put_reading(r: ReadingIn):
    if r.meter not in db.METERS:
        raise HTTPException(400, {"code": "bad_meter"})
    try:
        ts = datetime.strptime(r.ts.strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        raise HTTPException(400, {"code": "bad_time"})
    con = db.get_db()
    try:
        warnings = importer.check_meter(con, r.meter, ts, r.value)
        con.execute("""INSERT INTO meter_readings(ts,meter,value,note,method) VALUES(?,?,?,?,'manual')
                       ON CONFLICT(ts,meter) DO UPDATE SET value=excluded.value, note=excluded.note,
                       method='manual'""", (f"{ts:%Y-%m-%d %H:%M}", r.meter, r.value, r.note))
        con.commit()
    finally:
        con.close()
    return {"ok": True, "warnings": [w for w in warnings if w["code"] != "no_reference"]}


@app.delete("/api/readings")
def delete_reading(ts: str, meter: str):
    con = db.get_db()
    try:
        con.execute("DELETE FROM meter_readings WHERE ts=? AND meter=?", (ts, meter))
        con.commit()
    finally:
        con.close()
    return {"ok": True}


# ---------- Review of Gemma's results ----------

@app.get("/api/pending")
def pending(status: str = "pending"):
    con = db.get_db()
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM pending_reads WHERE status=? ORDER BY ts DESC LIMIT 200", (status,))]
    finally:
        con.close()
    for r in rows:
        r["payload"] = json.loads(r["payload"])
        r["issues"] = json.loads(r["issues"] or "[]")
        r["files"] = r["source_file"].split(";")
    return rows


class AcceptIn(BaseModel):
    override: dict | None = None


@app.post("/api/pending/{pid}/accept")
def accept(pid: int, body: AcceptIn | None = None):
    con = db.get_db()
    try:
        if not con.execute("SELECT 1 FROM pending_reads WHERE id=?", (pid,)).fetchone():
            raise HTTPException(404, {"code": "not_found"})
        importer.accept(con, pid, body.override if body else None)
    finally:
        con.close()
    return {"ok": True}


@app.post("/api/pending/{pid}/reject")
def reject(pid: int):
    con = db.get_db()
    try:
        importer.reject(con, pid)
    finally:
        con.close()
    return {"ok": True}


@app.get("/api/image")
def image(name: str):
    """A photo or screenshot by file name, only from the two configured folders."""
    con = db.get_db()
    try:
        folders = [db.folder(con, "photos_dir"), db.folder(con, "screenshots_dir")]
    finally:
        con.close()
    for f in filter(None, folders):
        f = f.resolve()
        p = (f / name).resolve()
        if p.parent == f and p.is_file():          # nothing outside the folders (no ../)
            return FileResponse(p)
    raise HTTPException(404, {"code": "not_found"})


# ---------- Import ----------

def _run_import(limit: int | None):
    con = db.get_db()
    try:
        todo = importer.jobs(con)[:limit]
        JOB.update(total=len(todo))
        for kind, paths in todo:
            r = importer.run_job(con, kind, paths)
            print(f"[import] {r['kind']} {r['source']}: {r['status']} {r['issues'] or ''}")
            JOB["done"] += 1
            JOB["log"].append(r)
    finally:
        con.close()
        JOB["running"] = False


@app.post("/api/import/scan")
def scan(limit: int | None = None):
    with JOB_LOCK:
        if JOB["running"]:
            raise HTTPException(400, {"code": "already_running"})
        if not gemma.available():
            raise HTTPException(400, {"code": "ollama_missing"})
        JOB.update(running=True, done=0, total=0, log=[])
    threading.Thread(target=_run_import, args=(limit,), daemon=True).start()
    return {"started": True}


@app.get("/api/import/progress")
def progress():
    return JOB


@app.post("/api/upload")
async def upload(kind: str = Form(...), file: UploadFile = File(...)):
    key = {"meter": "photos_dir", "shelly": "screenshots_dir"}.get(kind)
    name = Path(file.filename or "").name
    if not key or Path(name).suffix.lower() not in importer.IMG_EXT:
        raise HTTPException(400, {"code": "bad_file"})
    con = db.get_db()
    try:
        folder = db.folder(con, key)
    finally:
        con.close()
    if not folder:
        raise HTTPException(400, {"code": "no_folder"})
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(await file.read())
    return {"saved": name}


# ---------- Collector ----------

@app.get("/api/collector")
def collector():
    con = db.get_db()
    try:
        url = db.get_setting(con, "collector_url")
    finally:
        con.close()
    if not url:
        return {"ok": False, "error": "no_url"}
    return sync.health(url)


@app.post("/api/sync")
def do_sync():
    con = db.get_db()
    try:
        url = db.get_setting(con, "collector_url")
        if not url:
            raise HTTPException(400, {"code": "no_collector_url"})
        return sync.sync(con, url)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, {"code": "collector_unreachable", "info": str(e)})
    finally:
        con.close()


# ---------- Settings ----------

@app.get("/api/settings")
def get_settings():
    con = db.get_db()
    try:
        return db.all_settings(con)
    finally:
        con.close()


@app.put("/api/settings")
def put_settings(values: dict[str, str]):
    unknown = set(values) - set(db.DEFAULT_SETTINGS)
    if unknown:
        raise HTTPException(400, {"code": "unknown_setting", "info": ", ".join(sorted(unknown))})
    numeric = {k for k, v in db.DEFAULT_SETTINGS.items() if k not in ("photos_dir", "screenshots_dir",
                                                                      "collector_url", "auto_accept")}
    for k, v in values.items():
        if k in numeric and str(v).strip() != "":            # empty = back to the default
            try:
                if float(str(v).replace(",", ".")) <= 0:
                    raise ValueError
            except ValueError:
                raise HTTPException(400, {"code": "bad_number", "info": k})
            values[k] = str(v).replace(",", ".")
    if "collector_url" in values:
        values["collector_url"] = str(values["collector_url"]).strip().rstrip("/")
    con = db.get_db()
    try:
        for k, v in values.items():
            db.set_setting(con, k, str(v).strip())
        return db.all_settings(con)
    finally:
        con.close()
