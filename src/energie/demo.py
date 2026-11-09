# Copyright (c) 2026 Taras Bulubas. All rights reserved.
# Use requires prior written permission; see LICENSE.

"""Demo mode: the app on a throwaway database and image folders, filled with an invented house —
to try the app without data of your own, and to make the README screenshots. It never touches the
real database or folders: everything lives in a new temporary folder, whatever ENERGIE_DB,
ENERGIE_PHOTOS and ENERGIE_SCREENSHOTS say.

Every device name, reading and amount below is made up.
"""

from __future__ import annotations

import json
import random
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from energie import db

DAYS = 42            # days of meter readings
SHELLY_DAYS = 30     # days of Shelly 24 h totals (plugs bought later than the meters were read)
COUNTER_DAYS = 3     # days of collector counters

# device -> (floor, kWh per day function of (day index, weekday, rnd))
DEVICES = {
    "Freezer":         ("Basement",     lambda i, wd, r: 0.92 + r.gauss(0, 0.03)),
    "Heating pump":    ("Basement",     lambda i, wd, r: 0.12 + 0.012 * i + r.gauss(0, 0.02)),
    "Washing machine": ("Basement",     lambda i, wd, r: r.choice([0, 0, 0.85, 1.1]) if wd != 6 else 0),
    "Fridge":          ("Ground floor", lambda i, wd, r: 0.68 + r.gauss(0, 0.03)),
    "Dishwasher":      ("Ground floor", lambda i, wd, r: 1.02 if i % 2 == 0 else r.choice([0, 0.95])),
    "TV and media":    ("Ground floor", lambda i, wd, r: max(0.2, 0.45 + (0.3 if wd >= 5 else 0) + r.gauss(0, 0.1))),
    "Home office":     ("First floor",  lambda i, wd, r: 0.62 + r.gauss(0, 0.05) if wd < 5 else 0.08),
}


def _meter_image(path: Path, digits: str, red: str, unit: str):
    """A drawn meter face, so the review tab has something to show."""
    im = Image.new("RGB", (900, 700), (196, 198, 200))
    d = ImageDraw.Draw(im)
    font = ImageFont.load_default(size=72)
    small = ImageFont.load_default(size=34)
    d.rounded_rectangle((60, 60, 840, 640), 24, fill=(222, 224, 226), outline=(120, 120, 120), width=4)
    x = 120
    for ch in digits:
        d.rectangle((x, 180, x + 70, 290), fill=(20, 20, 20))
        d.text((x + 14, 192), ch, font=font, fill=(240, 240, 240))
        x += 76
    for ch in red:
        d.rectangle((x, 180, x + 70, 290), fill=(190, 30, 30))
        d.text((x + 14, 192), ch, font=font, fill=(250, 250, 250))
        x += 76
    d.text((x + 20, 210), unit, font=small, fill=(30, 30, 30))
    d.text((120, 380), "DEMO METER  -  invented", font=small, fill=(60, 60, 60))
    im.save(path, "JPEG", quality=88)


def build(folder: Path, today: date | None = None) -> Path:
    """Fill a new database in `folder` and point db at it; returns the database path."""
    today = today or date.today()
    rnd = random.Random(11)
    folder.mkdir(parents=True, exist_ok=True)
    photos, shots = folder / "photos", folder / "screenshots"
    photos.mkdir(exist_ok=True)
    shots.mkdir(exist_ok=True)
    db.DB_PATH = folder / "energie-demo.db"
    db.PHOTOS_DIR, db.SCREENSHOTS_DIR, db.COLLECTOR_URL = str(photos), str(shots), None
    db.init_db()
    con = db.get_db()
    db.set_setting(con, "photos_dir", str(photos))
    db.set_setting(con, "screenshots_dir", str(shots))

    start = datetime.combine(today - timedelta(days=DAYS), datetime.min.time())
    el, gas = 51234.0, 8123.456
    readings, periods = [], []
    for i in range(DAYS + 1):
        ts = start + timedelta(days=i, hours=7, minutes=rnd.randint(0, 50))
        if i:
            wd = (ts - timedelta(days=1)).weekday()
            per_dev = {n: max(0.0, f(i, wd, rnd)) for n, (_, f) in DEVICES.items()}
            unmetered = rnd.uniform(3.4, 6.2) + (3.5 if wd == 5 else 0)      # Saturday: oven and dryer
            el += sum(per_dev.values()) + unmetered
            gas += max(0.3, 0.55 + 0.022 * i + rnd.gauss(0, 0.08))           # heating season starts
            if i > DAYS - SHELLY_DAYS:
                w = f"{ts:%Y-%m-%d %H:%M}"
                src = f"Screenshot_{ts:%Y%m%d-%H%M%S}.png"
                floors: dict[str, float] = {}
                for n, kwh in per_dev.items():
                    fl = DEVICES[n][0]
                    floors[fl] = floors.get(fl, 0) + kwh * 1000
                    if kwh > 0.001:                       # the Shelly app leaves out a device with ~0 Wh
                        periods.append((w, "device", fl, n, round(kwh * 1000, 2), src))
                for fl, wh in floors.items():
                    periods.append((w, "floor", fl, fl, round(wh, 1), src))
                periods.append((w, "total", "", "total", round(sum(floors.values()), 1), src))
        readings.append((f"{ts:%Y-%m-%d %H:%M}", "electricity", round(el, 1), f"IMG_{ts:%Y%m%d_%H%M%S}_1.jpg"))
        readings.append((f"{ts:%Y-%m-%d %H:%M}", "gas", round(gas, 3), f"IMG_{ts:%Y%m%d_%H%M%S}_2.jpg"))
    con.executemany("INSERT INTO meter_readings(ts,meter,value,source_file,method) VALUES(?,?,?,?,'gemma')", readings)
    con.executemany("INSERT INTO shelly_periods VALUES(?,?,?,?,?,?)", periods)

    # collector counters: 15-minute samples with a daily profile per device
    counters, end = [], datetime.combine(today, datetime.min.time()) + timedelta(hours=7)
    totals = {n: rnd.uniform(20000, 90000) for n in DEVICES}
    t = end - timedelta(days=COUNTER_DAYS)
    while t <= end:
        h = t.hour + t.minute / 60
        for n in DEVICES:
            w = {"Freezer": 38 if (t.minute // 15) % 2 else 2, "Heating pump": 22 if 5 <= h < 23 else 9,
                 "Fridge": 30 if (t.minute // 15) % 3 == 0 else 2,
                 "Washing machine": 1800 if t.weekday() == 5 and 9 <= h < 10.5 else 0.8,
                 "Dishwasher": 1900 if 20 <= h < 21 else 0.6,
                 "TV and media": 110 if 18 <= h < 23 else 6.5,
                 "Home office": 140 if t.weekday() < 5 and 8 <= h < 17 else 4}[n]
            totals[n] += w * 0.25
            counters.append((f"{t:%Y-%m-%d %H:%M:%S}", n, round(totals[n], 2), w))
        t += timedelta(minutes=15)
    con.executemany("INSERT INTO shelly_counters VALUES(?,?,?,?)", counters)

    # one reading for review (Gemma misread a drum) and one new photo not read yet
    last = datetime.strptime(readings[-2][0], "%Y-%m-%d %H:%M")
    ts = last + timedelta(days=1)
    bad = f"IMG_{ts:%Y%m%d_%H%M%S}_1.jpg"
    good_value = round(el + 9.6, 1)
    _meter_image(photos / bad, f"{int(good_value):06d}", str(round(good_value * 10) % 10), "kWh")
    misread = good_value + 10000
    issue = [{"code": "increase_too_large", "delta": round(misread - el, 3), "days": 1.0, "limit": 60.0,
              "ref_ts": readings[-2][0]}]
    con.execute("INSERT INTO pending_reads(kind, source_file, ts, payload, issues) VALUES(?,?,?,?,?)",
                ("meter", bad, f"{ts:%Y-%m-%d %H:%M}",
                 json.dumps({"meter": "electricity", "black_digits": f"{int(misread):06d}",
                             "red_digits": str(round(misread * 10) % 10), "rolling": True, "value": misread}),
                 json.dumps(issue)))
    ts2 = ts + timedelta(minutes=1)
    _meter_image(photos / f"IMG_{ts2:%Y%m%d_%H%M%S}_2.jpg", f"{int(gas + 1.2):05d}", "418", "m³")
    con.commit()
    con.close()
    return db.DB_PATH


def run(host: str, port: int):
    import uvicorn

    folder = Path(tempfile.mkdtemp(prefix="energie-demo-"))
    path = build(folder)
    print(f"Demo database: {path}  (invented data; your own database is not used)")
    from energie.app import app
    uvicorn.run(app, host=host, port=port)

