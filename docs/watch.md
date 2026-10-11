# The hourly watch

`GET /watch` on the gateway runs plain-code checks on the live greenhouse. No model is involved in the checks, so a
paused watch costs nothing and the model only formats the result. The chat tool exposes it as `run_watch`.

## What it checks

| Check | Fires when | Severity |
|---|---|---|
| Missing / unavailable | a measurement role's entity has no state, or reads unavailable/unknown | amber |
| Stale | no report for `stale_minutes` (default 30) | amber |
| Flat | exactly the same reading for `flat_hours` (default 3) with enough history to say so | amber |
| Battery | a battery sensor on the same device is under 20% | amber |
| Below / above band | the greenhouse temperature has stayed under the low setpoint or over the high setpoint for `band_minutes` (default 20) | amber |
| Cold / hot alarm | the temperature is at or past the alarm limit helpers | red |
| Thermostat fault | the thermostat reason sensors report malfunction, emergency, overheat (red) or misconfiguration / stalled sensor (amber) | red / amber |

The temperature band is the live setpoint sensors (roles `target_temp_low` and `target_temp_high`), never a number
in the code. In the "Charging Earth Battery" mode the growing band is skipped. Humidity is reported for information
only. Roles that are not approved yet are listed under `roles_missing`, and the matching checks are skipped
rather than guessed. Not built yet: equipment commanded versus observed.

## Running it every hour

In Open WebUI open **Automations** (sidebar, or `/automations`) and create one:

- **Model:** SAGE Small (it has the gateway tool attached).
- **Schedule:** Hourly.
- **Prompt:** the text of `context/prompts/automation_watch.md`.
- Pick where the result goes, then use the run-now button once to check it. The tool call should show in the run.

Cost: about 10k tokens per run on the small role. Your logged first chat cost $0.0057 for 46k tokens, which puts
24 runs a day at a few cents. The daily cap on the small role (default $0.50) applies to these runs too.
For a cheaper watch, schedule it every 2 or 3 hours.
