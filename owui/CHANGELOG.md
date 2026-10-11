# Changelog: Open WebUI pieces

For the things you paste or import into Open WebUI by hand (they are not add-ons, so Home Assistant does not
show updates for them). The version is in each file's header.

## SAGE Gateway tool (`tools/greenhouse_gateway.py`)

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
