# Copyright (c) 2026 Taras Bulubas. All rights reserved.
# Use requires prior written permission; see LICENSE.

"""Compare Gemma's reading of meter photos and Shelly screenshots with hand-checked values.

    python -m energie eval --truth data --model gemma4:e4b --only meter

Ground truth: meter_readings.csv / shelly_readings.csv in the --truth folder (the same format as
`python -m energie load-csv`). The images come from the folders in the settings. Nothing is written
to the database; the result per item goes to eval-<model>-<time>.csv in the --truth folder.
"""
from __future__ import annotations

import csv
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from energie import db, gemma, importer

TOLERANCE = {"electricity": 0.15, "gas": 0.0015}     # +- one unit of the last digit (a rolling drum)


def _meters(truth: Path, photos: Path, limit: int | None, two_pass: bool,
            decimals: dict[str, int] | None = None) -> list[dict]:
    out = []
    rows = list(csv.DictReader((truth / "meter_readings.csv").open(encoding="utf-8")))[:limit]
    for t in rows:
        meter = db.METER_ALIASES.get(t["meter"].lower(), t["meter"].lower())
        p = photos / (t.get("source_file") or "")
        if not t.get("source_file") or not p.exists():
            continue
        t0 = time.time()
        try:
            r = gemma.read_meter(p.read_bytes(), two_pass=two_pass, decimals=decimals)
            got, kind, err = r["value"], r["meter"], ""
        except Exception as e:  # noqa: BLE001
            got, kind, err = None, "", f"{type(e).__name__}: {e}"
        dt = time.time() - t0
        exp = float(t["value"])
        ok = got is not None and kind == meter and abs(got - exp) <= TOLERANCE[meter]
        out.append({"kind": "meter", "file": p.name, "item": meter, "expected": exp, "got": got, "ok": ok,
                    "sec": round(dt, 1), "note": err or (f"read as {kind}" if kind != meter else "")})
        print(f"{'OK ' if ok else 'BAD'} {p.name:34} {meter:11} expected {exp:>11} got {got!s:>11}  {dt:5.1f}s")
    return out


def _shelly(truth: Path, shots: Path, limit: int | None) -> list[dict]:
    out = []
    expected: dict[str, dict] = defaultdict(dict)
    sources: dict[str, list[str]] = {}
    for r in csv.DictReader((truth / "shelly_readings.csv").open(encoding="utf-8")):
        name = "total" if r["scope"] == "total" else r["name"]
        expected[r["window_end"]][(r["scope"], name)] = float(r["energy_wh"])
        sources[r["window_end"]] = (r.get("source") or "").split(";")
    for window in list(expected)[:limit]:
        files = [shots / f for f in sources[window] if f and (shots / f).exists()]
        if not files:
            continue
        got: dict[tuple, float] = {}
        t0 = time.time()
        for f in files:
            try:
                cards = gemma.read_shelly_screenshot(f.read_bytes())["cards"]
            except Exception as e:  # noqa: BLE001
                print("error", f.name, e)
                continue
            for c in cards:
                if gemma.is_total(c["title"]):
                    if c["total_wh"] is not None:
                        got[("total", "total")] = c["total_wh"]
                    continue
                if c["total_wh"] is not None:
                    got[("floor", c["title"])] = c["total_wh"]
                for n, v in c["devices"].items():
                    got[("device", n)] = v
        dt = time.time() - t0
        n_ok = 0
        for key, exp in expected[window].items():
            g = got.get(key)
            if g is None:        # lenient name match (diacritics, names cut off with '…')
                short = key[1][:10].lower()
                g = next((v for (s, n), v in got.items()
                          if s == key[0] and n.replace("…", "").replace("...", "")[:10].lower() == short), None)
            ok = g is not None and abs(g - exp) <= max(1.0, exp * 0.006)   # kWh shown with 2 decimals
            n_ok += ok
            out.append({"kind": "shelly", "file": files[0].name, "item": f"{key[0]}:{key[1]}", "expected": exp,
                        "got": g, "ok": ok, "sec": round(dt / len(expected[window]), 1), "note": ""})
        print(f"{window}: {n_ok}/{len(expected[window])} values right, {len(files)} screenshots, {dt:.0f}s")
    return out


def run(truth: Path, only: str | None = None, limit: int | None = None, two_pass: bool = True) -> list[dict]:
    if not gemma.available():
        raise SystemExit(f"Ollama is not running ({gemma.OLLAMA_URL}).")
    db.init_db()
    con = db.get_db()
    photos, shots = db.folder(con, "photos_dir"), db.folder(con, "screenshots_dir")
    decimals = importer.meter_decimals(con)
    con.close()
    results: list[dict] = []
    if only in (None, "meter"):
        if not photos:
            raise SystemExit("No photos folder: set it on the Settings tab or with ENERGIE_PHOTOS.")
        results += _meters(truth, photos, limit, two_pass, decimals)
    if only in (None, "shelly"):
        if not shots:
            raise SystemExit("No screenshots folder: set it on the Settings tab or with ENERGIE_SCREENSHOTS.")
        results += _shelly(truth, shots, limit)

    out = truth / f"eval-{gemma.MODEL.replace(':', '_')}-{datetime.now():%Y%m%d-%H%M}.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["kind", "file", "item", "expected", "got", "ok", "sec", "note"])
        w.writeheader()
        w.writerows(results)
    print(f"\n=== {gemma.MODEL}{'' if two_pass else ' (single pass)'} ===")
    for k in ("meter", "shelly"):
        rs = [r for r in results if r["kind"] == k]
        if rs:
            n = sum(r["ok"] for r in rs)
            print(f"{k:7} accuracy {n}/{len(rs)} = {100 * n / len(rs):.0f} %   "
                  f"avg {sum(r['sec'] for r in rs) / len(rs):.1f} s/item")
    print("details:", out)
    return results
