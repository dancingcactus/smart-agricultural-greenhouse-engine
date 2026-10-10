# Greenhouse Gateway

A read-only bridge between Home Assistant and the Greenhouse AI Operator. It never changes anything
in Home Assistant. It records which entities are stored in VictoriaMetrics (and under what metric
names) and serves read-only history to the agent side.

## Configuration

| Option | What it does |
| --- | --- |
| `vm_url` | VictoriaMetrics address **as seen from this add-on**, e.g. `http://a0d7b954-victoriametrics:8428`. `localhost` means this add-on itself. |
| `vm_username`, `vm_password` | Only if VictoriaMetrics uses basic authentication. |
| `api_key` | If set, requests must send it as `X-Gateway-Key`. Recommended whenever port 8099 is enabled. |
| `as_of_override` | Testing only: treat this time as "now" for every query. Callers can only tighten it. |
| `metric_catalog_cron` | When the catalog is rebuilt (cron: minute hour day month weekday). Default daily 03:15. |
| `metric_catalog_lookback_days` | How far back to look for data, 1-1100. Default 365. |
| `catalog_ignore` | Entities to leave out of the "missing" counts. See below. |

## The ignore list

One pattern per line (the *+* button in the UI adds a line). A pattern is matched against the
**whole entity ID**, so it must include the domain (`sensor.`, `light.`, ...). Matching is
case-sensitive and uses wildcards, **not** regular expressions:

| Wildcard | Matches |
| --- | --- |
| `*` | any run of characters, including none and including dots |
| `?` | exactly one character |
| `[abc]` | one of `a`, `b` or `c` |
| `[!abc]` | any one character except `a`, `b`, `c` |

Examples:

| Pattern | Ignores |
| --- | --- |
| `sensor.small_screen_*` | every sensor whose ID starts with `sensor.small_screen_` |
| `*_battery` | every entity whose ID ends in `_battery` |
| `sensor.plant_sensor_p?_moisture` | `..._p1_moisture` and `..._p2_moisture` |
| `light.small_screen_display_backlight` | exactly that one entity |
| `small_screen_*` | **nothing**: it has no domain, so it can never match |

Ignored entities are marked `ignored`. They are still searchable and exported; they just stop
counting as `missing`. This also applies to orphans (data whose entity no longer exists).
Blank lines are skipped, and surrounding spaces are trimmed.

## Endpoints

See the project README for the full API (catalog, read-only Home Assistant and VictoriaMetrics
routes, call log). The API port (8099) is off by default; enable it on the Network tab only when
you need it, and set `api_key` first.

## Recording helpers into VictoriaMetrics

Home Assistant's InfluxDB integration only writes when a value changes, so a setpoint nobody touches
stops producing samples and can eventually age out of VictoriaMetrics' retention. This add-on can
write each helper's current value on a schedule instead, **directly to VictoriaMetrics**, under the
same series Home Assistant uses. It adds no entities to Home Assistant and no duplicate series.

| Option | What it does |
| --- | --- |
| `helper_snapshot_enabled` | Off by default. Turns the job on. |
| `helper_snapshot_entities` | Which helpers, same wildcards as the ignore list. Empty means nothing is written. |
| `helper_snapshot_cron` | Schedule; default every six hours. It also runs once at add-on start. |

This is the gateway's only write path. It writes only to VictoriaMetrics (never Home Assistant),
only `input_number` and `input_boolean` values, only for helpers matching your patterns, and there
is no API endpoint that can trigger it. Requirements: InfluxDB-style ingest, and a VictoriaMetrics
login that may write (`vm_username` / `vm_password`).

The series identity follows the InfluxDB integration: measurement = the unit (or the entity ID when
there is no unit), tags = `domain`, `entity_id` and the extra tags nearly all your series share, such
as `db=homeassistant`. A helper is skipped, not written, if the catalog shows it already has a series
under a different name. Run the catalog once before enabling.

To check before enabling, set `helper_snapshot_entities` and open
`/catalog/snapshot-preview`: it lists the exact lines that would be written and any helpers skipped
(for example a value that is `unavailable`), and writes nothing.

## Weather archive

Forecasts change, and a replay of a past day needs the forecast that was known *then*, not today's
view of that day. Add your Home Assistant weather entity (for example `weather.forecast_home`) to
`weather_entities` and the gateway saves, on `weather_cron` (default hourly) and at start-up, the
entity's current conditions and every forecast type it supports (hourly, daily, twice daily),
stamped with the pull time. An unchanged forecast is stored once. History starts when you turn it
on and cannot be filled in later.

| Option | What it does |
| --- | --- |
| `weather_entities` | Entities to archive, one per line. Empty means nothing is archived. |
| `weather_cron` | When to pull. Default hourly at ten past. |
| `weather_hourly_horizon_hours` | Hours ahead to keep from each hourly forecast. Default 72. |

Read it with `/weather/forecast`, `/weather/observations` and `/weather/status`. With an `X-As-Of`
header, `/weather/forecast` returns the latest forecast pulled at or before that time, or 404 if
none had been pulled yet; it never falls back to a later one. The gateway reads forecasts through a
read-only websocket subscription, not a service call.

## Configuration snapshot

With `config_snapshot_enabled` on, the gateway copies your Home Assistant configuration into a git
history inside the add-on (hourly by default, and at start-up) so the agent side can read it, and
read it as it was at any past time. The Home Assistant config folder is mapped into the add-on
read-only.

| Option | What it does |
| --- | --- |
| `config_snapshot_enabled` | Off by default. |
| `config_snapshot_cron` | When to snapshot. Default hourly at twenty past. |
| `protected_automations` | Patterns for automations that may never be agent-managed. |

**What is copied:** top-level `*.yaml` files, `packages/`, `automations/`, `scripts/`, `scenes/`,
`templates/` and `blueprints/`, plus helper and dashboard definitions from `.storage`. **Never
copied:** `secrets.yaml`, login and token files, integration credentials, certificates and keys.
**Blanked in the copies:** values under keys named like password, token, secret, api_key, and your
latitude and longitude. `!secret name` references stay (they hold no value).

**Safety scan:** before anything is committed, the copy is searched for every value in
`secrets.yaml` and for this add-on's own credentials. A single hit aborts the snapshot, leaves the
previous one untouched, and reports which file (never the value) via `/snapshot/status`. Values
shorter than four characters are not searchable; the status shows how many were skipped.

**Reading it:** `/snapshot/files`, `/snapshot/file?path=...`, `/snapshot/log`,
`/snapshot/automations`, `/snapshot/entity-references`, all honouring `X-As-Of`. `POST
/snapshot/run` takes one now. The commit date is the time the snapshot was taken, so a change
is never visible earlier than the gateway actually saw it.

## Entity glossary

Your entity ids are often not what a person calls things (for example which of several identical
meters is the GAHT exit sensor). The glossary holds a plain-English meaning for each recorded entity.
After every catalog run, each new entity gets a **draft** built from facts the catalog already knows
(name, unit, area, device class); you add the meaning.

Open the **Greenhouse** item in the Home Assistant sidebar to review: filter by status, edit the
meaning and aliases, then **Approve**, **Save as draft** or **Reject**. Entities whose names look
cryptic (hex suffixes, numbered duplicates) are listed first.

**Where an entity is used:** each entry shows the device it belongs to (name, manufacturer and
model, and the other entities on the same device), and the automations, scripts, scenes and
dashboards that reference it, with their names. The "used in" part comes from the configuration
snapshot, so turn that on and take a snapshot (`POST /snapshot/run`) first; it appears in the glossary
after the next catalog run. `GET /snapshot/entity-usage` returns the same data.

**Who can approve:** only requests that arrive through the sidebar panel, which Home Assistant has
already authenticated as an administrator. The API key can read the glossary and suggest draft
meanings (`POST /glossary/drafts`) but can never approve, reject or change an approved entry, even if
it is leaked. Approving is recorded with your name and time.

**For replays:** `GET /glossary` with an `X-As-Of` header returns only what was approved by that
time, so a replay is never told something an owner wrote later.
