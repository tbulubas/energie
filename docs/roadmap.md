# Roadmap

1. **Pick the model.** Run `python -m energie eval` with `gemma4:e4b`, `gemma4:26b-a4b-it-qat` and
   `gemma4:31b` on the hand-checked photos and screenshots; tune the prompts in `gemma.py` where
   they fail.
2. **Collector on a Raspberry Pi 4.** Clean Raspberry Pi OS Lite, then `make deploy` (Ansible, `deploy/`). From then on
   hourly profiles and night base load per plug; screenshots are no longer needed.
3. **Gas without photos.** Reed contact on the gas meter's pulse output (GPIO17), calibrate once.
   Then: daily gas against the outdoor temperature (heating degree days, e.g. from Open-Meteo),
   hot water as the base, heating as the slope.
4. **The whole house.** A Shelly Pro 3EM in the distribution board (installed by an electrician)
   removes the unmetered rest; meter photos stay as an occasional check against it.
5. **Bills.** Prices and gas factors per billing period; tariff comparison; payback of replacements
   (fridge, freezer, pumps, standby).

---

© 2026 Taras Bulubas. All rights reserved. See [LICENSE](../LICENSE).
