# Copyright (c) 2026 Taras Bulubas. All rights reserved.
# Use requires prior written permission; see LICENSE.

"""The Ollama client without Ollama: request shape, image handling and parsing."""

import io
import json

from PIL import Image

from energie import gemma


def png(w, h) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 200, 200)).save(buf, "PNG")
    return buf.getvalue()


class FakeResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"message": {"content": json.dumps(self.content)}}


def test_digits_value():
    assert gemma.digits_value("084768", "2") == 84768.2
    assert gemma.digits_value("15014", "350") == 15014.35
    assert gemma.digits_value("0 8 4", "") == 84.0


def test_tall_screenshot_is_cut_into_overlapping_bands():
    bands = gemma.tiles(Image.new("RGB", (1080, 2400)))
    assert len(bands) == 3 and all(b.size[0] == 1080 and b.size[1] <= 1000 for b in bands)


def test_crop_box_keeps_a_margin():
    crop = gemma.crop_box(Image.new("RGB", (1000, 1000)), (200, 400, 600, 450))
    assert crop.size == (520, 110)


def test_read_meter_two_passes(monkeypatch):
    sent = []

    def post(url, json=None, timeout=None):
        sent.append(json)
        props = json["format"]["properties"]
        if "box" in props:
            return FakeResponse({"meter": "gas", "box": [100, 300, 700, 400]})
        return FakeResponse({"meter": "gas", "black_digits": "15014", "red_digits": "350", "rolling": False})

    monkeypatch.setattr(gemma.requests, "post", post)
    r = gemma.read_meter(png(1200, 900))
    assert r["value"] == 15014.35 and r["two_pass"] and len(sent) == 2
    assert all(m["messages"][0]["images"] and m["options"]["temperature"] == 0 for m in sent)
    assert "probably the gas meter" in sent[1]["messages"][0]["content"]


def test_read_shelly_merges_bands(monkeypatch):
    answers = iter([
        {"cards": [{"title": "Gesamtenergie - Letzte 24 …", "total": 5.4, "total_unit": "kWh", "complete": True, "devices": []},
                   {"title": "Basement - Letzte 24 Stunden", "total": 1.2, "total_unit": "kWh", "complete": False,
                    "devices": [{"name": "Fridge", "value": 900.89, "unit": "Wh"}]}]},
        {"cards": [{"title": "Basement - Letzte 24 Stunden", "total": 1.2, "total_unit": "kWh", "complete": True,
                    "devices": [{"name": "Washer", "value": 0.28, "unit": "kWh"}]}]},
        {"cards": []}])
    monkeypatch.setattr(gemma.requests, "post", lambda url, json=None, timeout=None: FakeResponse(next(answers)))
    cards = {c["title"]: c for c in gemma.read_shelly_screenshot(png(1080, 2400))["cards"]}
    assert cards["Gesamtenergie"]["total_wh"] == 5400 and cards["Basement"]["total_wh"] == 1200
    assert cards["Basement"]["devices"] == {"Fridge": 900.89, "Washer": 280}


def test_total_titles():
    assert gemma.is_total("Gesamtenergie") and gemma.is_total("Total energy") and not gemma.is_total("Basement")
