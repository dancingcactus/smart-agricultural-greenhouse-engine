# Changelog: Open WebUI pieces

For the things you paste or import into Open WebUI by hand (they are not add-ons, so Home Assistant does not
show updates for them). The version is in each file's header.

## SAGE Gateway tool (`tools/greenhouse_gateway.py`)

- **0.5.2**: the embedded gateway client's `history_resampled` and `history_runtime` take `source`. Needs SAGE Gateway 0.16.0 for months of history.
- **0.5.1**: the embedded gateway client gains `history_resampled` and `history_runtime` (no new chat tools). Needs SAGE Gateway 0.15.0 or later for them.
- **0.5.0**: new `run_cadence` (which review loops the current mode allows; every scheduled run calls it first) and `run_daily_review` (the daily review's numbers). Needs SAGE Gateway 0.14.0 or later. Paste the new `context/prompts/automation_daily.md` into a daily Automation and update the watch Automation's prompt.
- **0.4.0**: two more inline visuals for the grower. `show_timeline`: when each piece of equipment ran (one lane per `equipment_*` role), lined up with the greenhouse temperature and its target band, with running time, starts and short runs (under 5 minutes) per lane. It shows what changed state and when, not why. `show_values`: number tiles for current readings, each with its unit, how long ago it updated, inside/below/above target (with a position meter for the greenhouse temperature), the change over the last hour and a 24 hour sparkline; stale or unavailable readings are flagged. Needs SAGE Gateway 0.12.0 or later and approved roles.
- **0.3.0**: new `show_chart`: draws a chart of recent readings inline in the chat for the grower (roles in, picture out; the model never types the data). Lines per role, a target band for the greenhouse temperature, a hover readout, a numbers table, light and dark. It returns the figures (latest, low, high, average, share of time in the target band) for the model to quote. Needs SAGE Gateway 0.12.0 or later and approved roles.
- **0.2.0**: new `get_roles` (which entity does which job) and `run_watch` (the code-only health checks). Needs
  SAGE Gateway 0.11.0 or later for roles and 0.12.0 for the watch.
- **0.1.0**: first version: catalog search, history, metrics, weather, config, glossary.

## SAGE Run Log filter (`functions/sage_run_log.py`)

- **0.1.3**: a daily-cap block is shown in the chat as an error and a pop-up.
- **0.1.2**: the prompt hash is read from the preset description (Open WebUI hides the system prompt from filters).
- **0.1.1**: logs why it skipped or could not reach the gateway.
- **0.1.0**: first version: stamps each answer in the gateway's run ledger and enforces daily caps.

## Presets (`presets/sage-presets.json`)

Rebuilt by `scripts/build_presets.py`; each preset's description ends with the hash of its prompt.

- The file now also holds one entry per base model (the four OpenRouter models) marked hidden, so after an import only
  the SAGE presets show in the model selector. Presets still use those models underneath. To switch it off, set
  `hide_base_models: false` in `owui/presets/models.yaml` and rebuild; to show a model again, use Admin Panel ->
  Settings -> Models -> Show Model.
