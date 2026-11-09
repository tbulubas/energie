# Energie — design

Background for whoever continues the project (people and Claude). `CLAUDE.md` holds the rules and
conventions; this file describes the goal and the architecture. Findings from the real data
are not here (the repository holds no real readings) but in `data/notes.md`; the history of the
requirements and decisions is in `docs/history.md`. Git ignores both.

## 1. Goal

A local AI application that analyses the energy use of a three-floor townhouse: electricity first,
then gas. Sources: Shelly smart plugs on some circuits, the main electricity meter, the gas meter.
It should work like the user's finance app (Financie): local, private, the numbers computed by code,
a local LLM for what only an LLM can do.

## 2. Architecture

```
 Shelly plugs ──LAN──► Raspberry Pi: collector (24/7) ──HTTP :8600──┐
 gas meter (reed) ─GPIO─►     collector.db                          │ sync on start
                                                                     ▼
 meter photos ───┐                                 PC: Energie (http://localhost:8510)
 screenshots ────┴──► Gemma (Ollama) ─► checks ─►  energie.db ─► analysis ─► dashboard + commentary
```

| Where | What |
|---|---|
| Raspberry Pi (basement, 24/7) | `collector/collector.py`: Shelly Gen1/Gen2/Gen3/Pro 3EM, gas pulses on GPIO17, SQLite, API on :8600 |
| OMEN PC | the app: import, review, analysis, dashboard; Gemma on the GPU |
| Odroid HC4 NAS | nightly backup of `collector.db` |

Data model (`energie.db`, schema v1): `meter_readings`, `shelly_periods` (24 h totals from
screenshots), `shelly_counters` (cumulative counters from the collector), `pending_reads` (Gemma
results waiting for review), `settings`.

Key rules (details in `CLAUDE.md`): Python computes every number; checks before storing (a register
never runs backwards, the daily increase stays below a limit, devices add up to rooms and rooms to the
house); a counter that drops means a restarted plug and counts from 0.

## 3. Open questions

- Which Raspberry Pi model exists (a Pi 4 was mentioned), and is there LAN or Wi-Fi next to the meters
  in the basement?
- Which generation are the Shelly plugs (Gen2+/Gen3 use `/rpc`, Gen1 `/status`), and do they have a
  password (the collector does not support Digest auth yet)?
- Prices and gas factors (Zustandszahl, Brennwert) from the bills — the Settings tab holds placeholders.
- Which model reads the meters well enough — to be measured with `python -m energie eval`.

## 4. Next steps

See `docs/roadmap.md`: pick the model, collector on the Pi, gas pulses and heating degree days, a
Shelly Pro 3EM for the whole house, prices per billing period.

---

© 2026 Taras Bulubas. All rights reserved. See [LICENSE](../LICENSE).
