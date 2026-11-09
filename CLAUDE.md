# Energie — project guide for Claude

Local-first home energy analysis. Everything runs on the user's machine and home network; no
utility accounts, no cloud APIs, no data to third parties. Treat that as non-negotiable.

## Privacy rules (read first)

- **Never commit or copy into tests:** `energie.db`, meter photos (`hodiny/`, `photos/`), Shelly
  screenshots (`screenshots/`), `data/` (hand-checked readings, eval results) or
  `collector/collector.toml` (device IPs). Meter photos show the meter number and the network
  operator's id; readings show when somebody is at home.
- Work on the code, not the data. Opening the user's photos or screenshots is fine only when the user
  asks for it in the conversation (e.g. to check a misread value); never add their values to tests,
  fixtures, the demo or the README. Invent samples.
- Screenshots for the README come from the demo only: `make screenshots`
  (`scripts/readme_screenshots.py`, headless Edge/Chrome on virtual time so the charts have finished
  animating; it opens tabs as `/?lang=en#<tab>`). The demo sets
  `db.DB_PATH`, `db.PHOTOS_DIR`, `db.SCREENSHOTS_DIR` to a new temporary folder, so it cannot touch
  real data. Keep folder paths and the user name out of the pictures (the Settings tab shows paths).
- The README and LICENSE write the e-mail as `taras.bulubas [at] gmail.com`.
- Before any commit, confirm `git status` shows code files only.

## Stack

- Python 3.12, FastAPI + uvicorn, SQLite, pandas, Pillow, vanilla HTML/JS (no build step)
- Chart.js 4.4.4 bundled in `static/vendor/` (with its MIT licence), served under `/static`
- Local LLM: Gemma 4 via Ollama at `http://localhost:11434`; default `gemma4:26b-a4b-it-qat`
  (digit reading needs accuracy), `gemma4:e4b` and `gemma4:31b` are also installed on the user's PC
- Runs at `http://localhost:8510` (Financie uses 8500/8501), demo at 8511
- Environment variables all start with `ENERGIE_`: `ENERGIE_DB`, `ENERGIE_PHOTOS`,
  `ENERGIE_SCREENSHOTS`, `ENERGIE_COLLECTOR_URL`, `ENERGIE_GEMMA_MODEL`, `ENERGIE_OLLAMA_URL`
- `collector/` runs on a Raspberry Pi / Odroid: Python standard library only (3.11+, `tomllib`),
  not part of the package; `gpiozero` only when the gas input is enabled

## Layout

Same standard as the user's `Financie`, `PlanningRopeBridge` and `PlanningLinesSweeping` projects:
`src/` package, `pyproject.toml`, Makefile, pytest, shared IntelliJ run configs, copyright header in
every source file.

| File | Purpose |
|---|---|
| `src/energie/app.py` | FastAPI routes + static serving; `init_db()` and the collector sync run in the lifespan |
| `src/energie/db.py` | schema, `PRAGMA user_version`, backups, `settings` with env/default fallback, CSV import |
| `src/energie/analysis.py` | every number shown: intervals, coverage, device stats, hourly counters, base load, findings |
| `src/energie/importer.py` | new files → Gemma → checks → `meter_readings` / `shelly_periods` or `pending_reads` |
| `src/energie/gemma.py` | Ollama client: meter in two passes (locate counter, read crop), screenshots in bands, commentary |
| `src/energie/sync.py` | incremental download from the collector API |
| `src/energie/evaluate.py` | `python -m energie eval`: accuracy of a model against hand-checked CSVs |
| `src/energie/cli.py` | `python -m energie [--host] [--port]`, `demo`, `load-csv`, `sync`, `eval` |
| `src/energie/demo.py` | invented house in a temp folder (`make demo`) |
| `src/energie/static/index.html` | entire frontend: markup, CSS, JS (English only) |
| `src/energie/static/i18n/` | UI strings: `en.json` (reference and fallback), `de.json`, `sk.json` |
| `collector/` | collector, systemd unit, installer, its own README |
| `tests/` | pytest; `conftest.py` gives every test a temp DB and folders and disables Ollama |
| `scripts/check_venv.py` | guards the Python version of `.venv` (shared with the other projects) |
| `scripts/readme_screenshots.py` | `make screenshots`: README images in `docs/images` from the demo |

## Conventions

- **Numbers are deterministic.** The LLM never computes consumption, coverage, costs or estimates;
  `analysis.py` does. Gemma reads images and writes the commentary from a JSON of computed facts.
- **Checks before storing.** A reading is stored only when it passes `check_meter()` (monotonic against
  the neighbouring readings, increase per day below `max_daily_<meter>`) and a screenshot only when
  `check_shelly()` passes (devices add up to the room, rooms to the house, with tolerances for the
  app's rounding). Anything else goes to `pending_reads` for the *Review* tab. With `auto_accept`
  off everything goes there.
- **Store counters, not power.** The collector stores the cumulative `aenergy.total` / `total_act`;
  hourly energy is the difference (`counter_hourly()`), a drop means a restarted plug and counts
  from 0. A collector outage loses resolution, never kWh.
- **Sync is incremental**: `settings.sync_since` / `sync_gas_since`, `INSERT OR IGNORE`.
- **Names from screenshots** are matched to known device/floor names (`importer.normalize()`): the app
  cuts long names with `…`.
- **Generic, not this house.** No device names, floor names, dates or meter models in code; derive
  from data (e.g. `full_windows()` decides from the floors present which Shelly windows count).
- **i18n:** the UI speaks English, German and Slovak. Every UI string lives in
  `static/i18n/<lang>.json` as `"key": "text with {name} placeholders"`; `index.html` only holds keys
  (`data-i18n="key"`, `data-i18n-ph`, `t('key', {name: value})`). Add a new string to all three files
  with the same placeholders; `en.json` is the fallback. The language comes from `?lang=`, then the
  stored choice, then the browser, else English. The backend returns codes, never user-facing text:
  errors as `HTTPException(400, {"code": ...})`, check results and findings as
  `{"code": ..., <numbers>}`; the frontend words them (`err_*`, `is_*`, `in_*`) and formats numbers
  and dates per language. `tests/test_i18n.py` checks all of this. A new language = a new JSON file
  plus an entry in `LOCALE` (`index.html`) and `gemma.LANGUAGES`.
- **Language:** talk to the user in Slovak. All code, comments, docstrings, log messages, LLM prompts,
  the README and other docs are in English; so are the database codes (`electricity`, `gas`, `total`,
  `floor`, `device`). Only `de.json`/`sk.json` and the user's own names are in other languages;
  `test_i18n.py` fails on German or Slovak letters in the code.
- **Settings** live in the `settings` table (Settings tab); `db.get_setting()` falls back to the
  `ENERGIE_*` variable, then to `DEFAULT_SETTINGS`. Prices and gas factors there are placeholders until
  the user enters the values from the bills.
- **Schema changes go through migrations.** Bump `SCHEMA_VERSION`; `init_db()` backs the database up
  (`<stem>-backup-v<old>-<timestamp>.db`) before migrating. Never require a re-import.
- **Time** comes from the file name (`IMG_YYYYMMDD_HHMMSS…`, `Screenshot_YYYYMMDD-HHMMSS…`), else EXIF
  `DateTimeOriginal`, else mtime. Screenshots within 10 minutes form one 24 h window.

## Running and testing

```bash
make venv      # .venv with Python 3.12 via the py launcher, editable install with [dev]
make test
make run       # or start.bat, which runs .venv\Scripts\python -m energie
make demo
```

The user is on Windows 11 with a local `.venv`; do not use or suggest conda. Dependencies live in
`pyproject.toml` only (no `requirements.txt`); after changing them run `make venv`.

Tests must never touch the real DB or folders: `conftest.py` drops all `ENERGIE_*` variables before the
package is imported, the autouse `temp_db` fixture points `db.DB_PATH`, `db.PHOTOS_DIR`,
`db.SCREENSHOTS_DIR` at `tmp_path` and clears `db.COLLECTOR_URL`, and Gemma is replaced by fixed
answers where a test needs it. The collector is tested against invented Shelly HTTP servers on
`127.0.0.1` (`tests/test_collector.py`).

## The user's setup (context, not code)

- 3-floor townhouse; Ferraris electricity meter and a diaphragm gas meter with a pulse output
  (0.1 m³ per pulse); Shelly plugs on three floors, named in Slovak in the Shelly app.
- Collector planned on a Raspberry Pi in the basement (gas pulses on GPIO17), backup to the Odroid NAS.
- Hand-checked readings in `data/` serve as ground truth for `python -m energie eval`.
- Background: `docs/energie-design.md` (goal, architecture, open questions); `docs/history.md`
  (the user's requests and the decisions taken — private, not in git); `data/notes.md` (findings
  from the real data — private, not in git). Read them before larger changes.
