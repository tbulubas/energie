# Copyright (c) 2026 Taras Bulubas. All rights reserved.
# Use requires prior written permission; see LICENSE.

"""Take the README screenshots in docs/images from the demo (invented data only).

Starts `python -m energie demo` on its own port, opens each tab in a headless Edge or Chrome and
saves a PNG. The browser runs on virtual time with a budget, so the data is loaded and the Chart.js
animations have finished before the picture is taken (a real-time screenshot can catch the charts
still empty). The demo never touches the real database or folders.

Usage: python scripts/readme_screenshots.py [--browser PATH] [--port 8519]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "images"
WIDTH = 1200
# file name -> (tab, height in px)
SHOTS = {"overview": ("overview", 1440), "devices": ("devices", 1330), "review": ("review", 340)}

# Chrome first: Edge leaves files in its temporary profile that Windows lets go of only later
CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]
NAMES = ["google-chrome", "chromium", "chromium-browser", "chrome", "msedge", "microsoft-edge"]


def find_browser() -> str | None:
    for path in CANDIDATES:
        if os.path.isfile(path):
            return path
    return next((p for p in map(shutil.which, NAMES) if p), None)


def wait_for(url: str, seconds: float = 60) -> None:
    deadline = time.monotonic() + seconds
    while True:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except OSError:
            if time.monotonic() > deadline:
                raise SystemExit(f"The demo did not answer at {url}")
            time.sleep(0.5)


def screenshot(browser: str, url: str, out: Path, height: int, profile: str) -> None:
    out.unlink(missing_ok=True)
    subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                    "--no-default-browser-check", "--force-device-scale-factor=1",
                    f"--user-data-dir={profile}", f"--window-size={WIDTH},{height}",
                    "--virtual-time-budget=10000", f"--screenshot={out}", url],
                   check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not out.is_file():
        raise SystemExit(f"The browser wrote no screenshot to {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--browser", help="path to Edge or Chrome (found automatically otherwise)")
    parser.add_argument("--port", type=int, default=8519, help="port for the demo (default 8519)")
    args = parser.parse_args()

    browser = args.browser or find_browser()
    if not browser:
        raise SystemExit("No Edge or Chrome found; pass --browser PATH")
    base = f"http://127.0.0.1:{args.port}"
    demo = subprocess.Popen([sys.executable, "-m", "energie", "demo", "--port", str(args.port)], cwd=ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    profile = tempfile.mkdtemp(prefix="energie-shots-")
    try:
        wait_for(base + "/api/status")
        OUT.mkdir(parents=True, exist_ok=True)
        for name, (tab, height) in SHOTS.items():
            out = OUT / f"{name}.png"
            screenshot(browser, f"{base}/?lang=en#{tab}", out, height, profile)
            print(f"{out.relative_to(ROOT)}  {WIDTH}x{height}")
    finally:
        demo.terminate()
        demo.wait(timeout=10)
        # on Windows, Edge's helper processes hold profile files for a moment after it exits
        # (TemporaryDirectory(ignore_cleanup_errors=True) still raises there in Python 3.12)
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    main()
