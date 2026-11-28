# Copyright (c) 2026 Taras Bulubas. All rights reserved.
# Use requires prior written permission; see LICENSE.

"""Client for the local Gemma model via the Ollama API (http://localhost:11434).
No data leaves your machine.

Gemma does two things only: it reads images (meter photos, Shelly app screenshots) and it writes
a commentary on numbers that analysis.py has already computed. It never computes anything itself.
"""
import base64
import io
import json
import os

import requests
from PIL import Image, ImageOps

OLLAMA_URL = os.environ.get("ENERGIE_OLLAMA_URL") or "http://localhost:11434"
# Reading digits needs accuracy more than speed, hence the 26B MoE model by default
MODEL = os.environ.get("ENERGIE_GEMMA_MODEL") or "gemma4:26b-a4b-it-qat"

# card titles of the house total in the Shelly app, by app language
TOTAL_TITLES = ("gesamt", "total", "celkov", "overall")

LANGUAGES = {"sk": "Slovak", "de": "German", "en": "English"}


def available() -> bool:
    try:
        requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        return True
    except requests.RequestException:
        return False


def chat(prompt: str, images: list[bytes] | None = None, json_schema: dict | None = None,
         timeout: int = 600) -> str:
    msg = {"role": "user", "content": prompt}
    if images:
        msg["images"] = [base64.b64encode(i).decode() for i in images]
    payload = {"model": MODEL, "messages": [msg], "stream": False,
               "options": {"temperature": 0.0, "num_ctx": 8192}}
    if json_schema:
        payload["format"] = json_schema
        # No thinking for structured answers: Gemma 4 can think about a meter counter until the
        # context is full and then return nothing; reading what is in a picture needs no reasoning,
        # and without it a read takes seconds instead of minutes.
        payload["think"] = False
    r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=timeout)
    r.raise_for_status()
    out = r.json()
    content = out["message"]["content"]
    if json_schema and not content.strip():
        raise ValueError(f"empty answer from {MODEL} (done_reason: {out.get('done_reason')})")
    return content


# ---------- images ----------

def load_image(data: bytes) -> Image.Image:
    return ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")


def to_jpeg(im: Image.Image, max_side: int = 1280) -> bytes:
    im = im.copy()
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def is_total(title: str) -> bool:
    return title.strip().lower().startswith(TOTAL_TITLES)


# ---------- meters ----------

# Gemma gives boxes the way it was trained: box_2d = [ymin, xmin, ymax, xmax], 0-1000. Asked for
# [x0, y0, x1, y1] it still answers in its own order, and the crop misses the counter.
BBOX_SCHEMA = {
    "type": "object",
    "properties": {
        "meter": {"type": "string", "enum": ["electricity", "gas", "unknown"]},
        "box_2d": {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4,
                   "description": "[ymin, xmin, ymax, xmax] of the counter, 0-1000"},
    },
    "required": ["meter", "box_2d"],
}

# All digits in one row: asked for black and red digits apart, Gemma often counts a red drum as a
# black one (a gas reading 100 times too large). Where the decimal point goes is a property of the
# meter, not of the photo: the settings say how many digits are decimals (DECIMALS by default).
METER_SCHEMA = {
    "type": "object",
    "properties": {
        "meter": {"type": "string", "enum": ["electricity", "gas"]},
        "digits": {"type": "string",
                   "description": "every digit of the counter, left to right, black and red, no separators"},
        "rolling": {"type": "boolean", "description": "the last digit is between two values"},
    },
    "required": ["meter", "digits", "rolling"],
}
DECIMALS = {"electricity": 1, "gas": 3}                 # red drums of a Ferraris meter / a gas meter

METER_HINT = (
    "An electricity meter counts kWh: black digits are whole kWh, the red digit(s) are tenths. "
    "A gas meter counts m³: black digits are whole m³, the red digits (usually three) are decimals. "
)


def locate_counter(img: Image.Image) -> tuple[str, tuple[int, int, int, int] | None]:
    """Which meter, and the box of its counter as (x0, y0, x1, y1) 0-1000 (None if implausible).
    A photo taken sideways is turned upright by load_image() from its EXIF orientation."""
    prompt = ("The photo shows an electricity meter or a gas meter. " + METER_HINT +
              "Tell which meter it is and give the bounding box box_2d ([ymin, xmin, ymax, xmax], 0-1000) "
              "of the mechanical counter with the digits.")
    out = json.loads(chat(prompt, images=[to_jpeg(img, 1024)], json_schema=BBOX_SCHEMA))
    y0, x0, y1, x1 = out["box_2d"]
    # a counter is wide and low; anything else is a miss, and the whole picture is read instead
    ok = 0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000 and (x1 - x0) > 50 and (x1 - x0) > (y1 - y0)
    return out["meter"], ((x0, y0, x1, y1) if ok else None)


def crop_box(img: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    """Crop a 0-1000 box with a margin around it (the counter is wide and low; the box often ends
    just before the red drums, hence the wide margin at the sides)."""
    W, H = img.size
    x0, y0, x1, y1 = box
    mx, my = (x1 - x0) * 0.3, (y1 - y0) * 0.6
    return img.crop((max(0, (x0 - mx) * W / 1000), max(0, (y0 - my) * H / 1000),
                     min(W, (x1 + mx) * W / 1000), min(H, (y1 + my) * H / 1000)))


def digits_value(black: str, red: str) -> float:
    black = "".join(ch for ch in black if ch.isdigit())
    red = "".join(ch for ch in red if ch.isdigit())
    return float(f"{int(black or 0)}.{red or '0'}")


def read_meter(data: bytes, two_pass: bool = True, decimals: dict[str, int] | None = None) -> dict:
    """Read a meter photo in two passes: find the counter, then read the enlarged crop.
    `decimals`: how many of the counter's digits are decimals, per meter (default DECIMALS)."""
    img = load_image(data)
    crop, hint = img, None
    if two_pass:
        try:
            hint, box = locate_counter(img)
            if box:
                crop = crop_box(img, box)
        except Exception as e:  # noqa: BLE001 - fall back to the whole picture
            print(f"[gemma] locate_counter failed: {e}")
    prompt = (METER_HINT + (f"This is probably the {hint} meter. " if hint in ("electricity", "gas") else "") +
              "Write every digit of the counter, left to right, black and red together. If a drum is between "
              "two digits, write the LOWER one and set rolling=true. Do not guess: write only what you see.")
    out = json.loads(chat(prompt, images=[to_jpeg(crop, 1280)], json_schema=METER_SCHEMA))
    digits = "".join(ch for ch in out["digits"] if ch.isdigit())
    k = {**DECIMALS, **(decimals or {})}.get(out["meter"], 0)
    out["black_digits"], out["red_digits"] = (digits[:-k], digits[-k:]) if k else (digits, "")
    out["value"] = digits_value(out["black_digits"], out["red_digits"])
    out["two_pass"] = crop is not img
    return out


# ---------- Shelly screenshots ----------

SHELLY_SCHEMA = {
    "type": "object",
    "properties": {
        "date_range": {"type": ["string", "null"]},
        "cards": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "the house total card or a room/floor name"},
                "total": {"type": "number"},
                "total_unit": {"type": "string", "enum": ["Wh", "kWh"]},
                "complete": {"type": "boolean", "description": "the card's total is fully visible"},
                "devices": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "value": {"type": "number"},
                                   "unit": {"type": "string", "enum": ["Wh", "kWh"]}},
                    "required": ["name", "value", "unit"]}},
            },
            "required": ["title", "total", "total_unit", "complete", "devices"]}},
    },
    "required": ["cards"],
}


def tiles(img: Image.Image, tile_h: int = 1000, overlap: int = 250) -> list[Image.Image]:
    """Cut a tall phone screenshot (1080x2400) into overlapping bands: a vision model scales the
    whole picture down, which makes the small text of the device list unreadable."""
    W, H = img.size
    top, bottom = int(H * 0.12), int(H * 0.87)        # without the status bar and the bottom navigation
    out, y = [], top
    while y < bottom:
        out.append(img.crop((0, y, W, min(bottom, y + tile_h))))
        if y + tile_h >= bottom:
            break
        y += tile_h - overlap
    return out


def _wh(value: float, unit: str) -> float:
    return value * (1000 if unit == "kWh" else 1)


def read_shelly_screenshot(data: bytes) -> dict:
    img = load_image(data)
    prompt = ("This is a band of a Shelly app screenshot (history, last 24 hours). For every card "
              "(the house total or a room) give its title, its total with the unit shown, and the list of "
              "devices with value and unit. Mark a card whose total is cut off with complete=false. "
              "Leave out devices cut off by the edge. Give the date range if it is visible.")
    cards: dict[str, dict] = {}
    date_range = None
    for t in tiles(img):
        out = json.loads(chat(prompt, images=[to_jpeg(t, 1280)], json_schema=SHELLY_SCHEMA))
        date_range = date_range or out.get("date_range")
        for c in out["cards"]:
            key = c["title"].split(" - ")[0].strip()
            cur = cards.setdefault(key, {"title": key, "total_wh": None, "devices": {}})
            if c.get("complete") and cur["total_wh"] is None:
                cur["total_wh"] = _wh(c["total"], c["total_unit"])
            for d in c["devices"]:
                cur["devices"][d["name"]] = _wh(d["value"], d["unit"])
    return {"date_range": date_range, "cards": list(cards.values())}


# ---------- commentary ----------

def commentary(facts: dict, lang: str = "en") -> str:
    prompt = (
        "You are an energy advisor for a family house (electricity, and gas for heating and hot water). "
        "Below are COMPUTED facts as JSON. Write a short commentary of at most 8 bullet points: what matters, "
        "what is unusual, concrete recommendations with the estimated saving in kWh and EUR. Use ONLY numbers "
        "from the JSON, invent nothing. Say so when a yearly estimate rests on a short period. "
        f"Write in {LANGUAGES.get(lang, 'English')}.\n\n" + json.dumps(facts, ensure_ascii=False, default=str))
    return chat(prompt)
