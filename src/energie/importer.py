# Copyright (c) 2026 Taras Bulubas. All rights reserved.
# Use requires prior written permission; see LICENSE.

"""Import of meter photos and Shelly app screenshots: Gemma reads them, deterministic checks
decide whether a result goes straight into the database or waits for review.

Checks (no LLM):
  meter:  the register never decreases against neighbouring readings, and the increase per day
          stays below the limit from the settings
  Shelly: devices add up to their room, rooms add up to the house total
Issues are codes with numbers ({"code": "...", ...}); the frontend words them.
"""
import difflib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from energie import db, gemma

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}
TS_RE = re.compile(r"(20\d{6})[_-](\d{6})")       # IMG_20261010_074140..., Screenshot_20261010-075153


# ---------- files ----------

def file_ts(path: Path) -> datetime:
    """Time the photo was taken: from the file name, else EXIF, else the file's mtime."""
    m = TS_RE.search(path.name)
    if m:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    try:
        from PIL import Image
        exif = Image.open(path).getexif().get_ifd(0x8769)
        if 36867 in exif:                                       # DateTimeOriginal
            return datetime.strptime(exif[36867], "%Y:%m:%d %H:%M:%S")
    except Exception:  # noqa: BLE001
        pass
    return datetime.fromtimestamp(path.stat().st_mtime)


def new_files(con) -> dict[str, list[Path]]:
    known = db.known_sources(con)
    out = {}
    for kind, key in (("meter", "photos_dir"), ("shelly", "screenshots_dir")):
        folder = db.folder(con, key)
        files = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMG_EXT) if folder and folder.is_dir() else []
        out[kind] = [p for p in files if p.name not in known]
    return out


def group_screenshots(files: list[Path], gap_min: int = 10) -> list[list[Path]]:
    """Screenshots taken within 10 minutes of each other show the same 24 h window."""
    groups: list[list[Path]] = []
    for p in sorted(files, key=file_ts):
        if groups and file_ts(p) - file_ts(groups[-1][-1]) <= timedelta(minutes=gap_min):
            groups[-1].append(p)
        else:
            groups.append([p])
    return groups


def jobs(con) -> list[tuple[str, list[Path]]]:
    nf = new_files(con)
    return [("meter", [p]) for p in nf["meter"]] + [("shelly", g) for g in group_screenshots(nf["shelly"])]


def normalize(name: str, candidates: list[str]) -> str:
    """Match a name as read from a screenshot to a known one ('Office-2nd-Fl…' -> 'Office-2nd-Floor')."""
    n = name.replace("…", "").replace("...", "").strip()
    prefix = [c for c in candidates if c.lower().startswith(n.lower())] if len(n) >= 6 else []
    if len(prefix) == 1:
        return prefix[0]
    m = difflib.get_close_matches(n, candidates, n=1, cutoff=0.75)
    return m[0] if m else n


def known_names(con, scope: str) -> list[str]:
    return [r[0] for r in con.execute("SELECT DISTINCT name FROM shelly_periods WHERE scope=?", (scope,))]


# ---------- meters ----------

def check_meter(con, meter: str, ts: datetime, value: float) -> list[dict]:
    issues = []
    limit = db.get_float(con, f"max_daily_{meter}")
    t = ts.strftime("%Y-%m-%d %H:%M")
    prev = con.execute("SELECT ts, value FROM meter_readings WHERE meter=? AND ts<? ORDER BY ts DESC LIMIT 1",
                       (meter, t)).fetchone()
    nxt = con.execute("SELECT ts, value FROM meter_readings WHERE meter=? AND ts>? ORDER BY ts LIMIT 1",
                      (meter, t)).fetchone()
    if not prev and not nxt:
        issues.append({"code": "no_reference"})
    for r, sign in ((prev, 1), (nxt, -1)):
        if not r:
            continue
        days = max(abs((ts - datetime.strptime(r["ts"], "%Y-%m-%d %H:%M")).total_seconds()) / 86400, 1 / 24)
        delta = (value - r["value"]) * sign
        if delta < 0:
            issues.append({"code": "counter_decreases", "ref_ts": r["ts"], "ref_value": r["value"]})
        elif delta > limit * days:
            issues.append({"code": "increase_too_large", "delta": round(delta, 3), "days": round(days, 1),
                           "limit": limit, "ref_ts": r["ts"]})
    return issues


def process_meter(con, path: Path) -> dict:
    ts = file_ts(path)
    r = gemma.read_meter(path.read_bytes())
    issues = check_meter(con, r["meter"], ts, r["value"])
    if r.get("rolling"):
        r["note"] = "rolling digit"
    return store(con, "meter", path.name, ts, r, issues)


# ---------- Shelly ----------

def check_shelly(cards: list[dict]) -> list[dict]:
    issues = []
    total = next((c["total_wh"] for c in cards if gemma.is_total(c["title"])), None)
    floors = [c for c in cards if not gemma.is_total(c["title"])]
    for c in floors:
        if c["total_wh"] is None:
            issues.append({"code": "missing_floor_total", "floor": c["title"]})
            continue
        s = sum(c["devices"].values())
        if c["devices"] and abs(s - c["total_wh"]) > max(60, 0.05 * c["total_wh"]):
            issues.append({"code": "devices_mismatch", "floor": c["title"], "devices_wh": round(s),
                           "total_wh": round(c["total_wh"])})
    if total is None:
        issues.append({"code": "missing_house_total"})
    elif floors and all(c["total_wh"] for c in floors):
        s = sum(c["total_wh"] for c in floors)
        if abs(s - total) > max(100, 0.05 * total):
            issues.append({"code": "floors_mismatch", "floors_wh": round(s), "total_wh": round(total)})
    return issues


def process_shelly_group(con, paths: list[Path]) -> dict:
    floors, devices = known_names(con, "floor"), known_names(con, "device")
    merged: dict[str, dict] = {}
    for p in paths:
        for c in gemma.read_shelly_screenshot(p.read_bytes())["cards"]:
            title = c["title"] if gemma.is_total(c["title"]) else normalize(c["title"], floors)
            cur = merged.setdefault(title, {"title": title, "total_wh": None, "devices": {}})
            cur["total_wh"] = cur["total_wh"] or c["total_wh"]
            cur["devices"].update({normalize(k, devices): v for k, v in c["devices"].items()})
    cards = list(merged.values())
    return store(con, "shelly", ";".join(p.name for p in paths), file_ts(paths[0]), {"cards": cards},
                 check_shelly(cards))


# ---------- store / review ----------

def store(con, kind: str, source: str, ts: datetime, payload: dict, issues: list[dict]) -> dict:
    t = ts.strftime("%Y-%m-%d %H:%M")
    cur = con.execute("INSERT OR REPLACE INTO pending_reads(kind, source_file, ts, payload, issues, status) "
                      "VALUES(?,?,?,?,?,'pending')",
                      (kind, source, t, json.dumps(payload, ensure_ascii=False), db.issues_json(issues)))
    con.commit()
    status = "pending"
    if not issues and db.get_setting(con, "auto_accept") == "1":
        accept(con, cur.lastrowid)
        status = "accepted"
    return {"source": source, "kind": kind, "status": status, "issues": issues}


def accept(con, pid: int, override: dict | None = None):
    row = con.execute("SELECT * FROM pending_reads WHERE id=?", (pid,)).fetchone()
    p = json.loads(row["payload"])
    if override:
        p.update(override)
    if row["kind"] == "meter":
        con.execute("INSERT OR REPLACE INTO meter_readings(ts,meter,value,source_file,note,method) VALUES(?,?,?,?,?,?)",
                    (row["ts"], p["meter"], float(p["value"]), row["source_file"], p.get("note", ""),
                     "manual" if override else "gemma"))
    else:
        put = "INSERT OR REPLACE INTO shelly_periods VALUES(?,?,?,?,?,?)"
        for c in p["cards"]:
            if gemma.is_total(c["title"]):
                if c["total_wh"] is not None:
                    con.execute(put, (row["ts"], "total", "", "total", c["total_wh"], row["source_file"]))
                continue
            if c["total_wh"] is not None:
                con.execute(put, (row["ts"], "floor", c["title"], c["title"], c["total_wh"], row["source_file"]))
            for name, wh in c["devices"].items():
                con.execute(put, (row["ts"], "device", c["title"], name, wh, row["source_file"]))
    con.execute("UPDATE pending_reads SET status='accepted', payload=? WHERE id=?",
                (json.dumps(p, ensure_ascii=False), pid))
    con.commit()


def reject(con, pid: int):
    con.execute("UPDATE pending_reads SET status='rejected' WHERE id=?", (pid,))
    con.commit()


def run_job(con, kind: str, paths: list[Path]) -> dict:
    """One file or screenshot group; an error is reported, not raised, so the others go on."""
    try:
        return process_meter(con, paths[0]) if kind == "meter" else process_shelly_group(con, paths)
    except Exception as e:  # noqa: BLE001
        return {"source": ";".join(p.name for p in paths), "kind": kind, "status": "error",
                "issues": [{"code": "read_failed", "info": f"{type(e).__name__}: {e}"}]}
