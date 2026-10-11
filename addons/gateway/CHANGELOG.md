# Changelog

All notable changes to the SAGE Gateway add-on. Versions follow `version` in `config.yaml`.

## 0.11.0

- Roles: a glossary alias written `role:<name>` (for example `role:greenhouse_temperature`) now names the job an entity does. `GET /glossary/roles` lists each role with the approved entities that carry it (drafts with `include_drafts=true`; with an as-of time, as approved then). Malformed role aliases are refused. The SAGE panel text explains the convention, and nothing else on the page changes.

## 0.10.0

- New run ledger: `POST /runs` records which role, model, prompt version and tool set produced each answer, with tokens and cost; `GET /runs` lists recent rows and `GET /runs/spend` totals calls and dollars per role over a window. The SAGE Run Log filter in Open WebUI uses it to stamp every answer and to enforce daily spend and call caps. The gateway only stores the rows; it is still read-only toward Home Assistant.

## 0.9.1

- Renamed to **SAGE Gateway** (Smart Agricultural Greenhouse Engine); the sidebar item is now **SAGE**. The add-on slug, options and data are unchanged, so nothing needs reconfiguring.

## 0.9.0

- Glossary entries show each entity's device and the automations, scripts and scenes that use it.

## 0.8.0

- Entity glossary with drafts built from the catalog, owner-only approval, version history and a sidebar panel.

## 0.7.0

- Home Assistant configuration snapshot: a git-backed mirror with secrets redacted, a pre-commit secret scan and an automation catalog.

## 0.6.0

- Weather archive: forecasts are stored as issued, so past forecasts can be read as they were at the time (`X-As-Of`).

## 0.5.1

- Helper snapshots use the same tags as the existing series (`db=homeassistant`) and refuse to run without a catalog run, so they never split a series.

## 0.5.0

- Rarely-changing helpers (`input_*`) are recorded straight into VictoriaMetrics on a schedule, without creating anything in Home Assistant.

## 0.4.1

- Every add-on option has help text on the settings screen, including the ignore-list syntax.

## 0.4.0

- Catalog ignore list and a 365-day lookback by default.

## 0.3.0

- Read-only API: states, history, registries, automation traces and VictoriaMetrics queries, with as-of clipping, a guard against reading the future, a call log and an `X-Gateway-Key`.

## 0.2.0

- Catalog classifies each series field, explains why an entity is missing from VictoriaMetrics and reports the true start of history.

## 0.1.4

- Optional basic auth for VictoriaMetrics.

## 0.1.3

- Connection errors name the VictoriaMetrics URL and are logged concisely.

## 0.1.2

- Docker init disabled so the base image's service supervisor runs as PID 1.

## 0.1.1

- Installable as a Home Assistant add-on.

## 0.1.0

- First version: entity-to-VictoriaMetrics metric catalog (searchable, scheduled, with CSV/JSONL export).
