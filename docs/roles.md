# Roles

A role names the job an entity does in the greenhouse, so the watch, the reviews and the agent can ask for
"the main greenhouse temperature sensor" instead of hard-coding entity ids. A role is an alias on a glossary entry
written `role:<name>` (lowercase letters, digits, underscores). Owners add it in the **SAGE** panel in the Home
Assistant sidebar; only approved entries count. `GET /glossary/roles` lists them and the chat tool exposes
`get_roles`. Several entities may share a role (for example, two exhaust fans).

Seed the first set with `scripts/seed_roles.py roles.yaml` (drafts only), then approve them in the panel.

## Standard roles

Measurements: `greenhouse_temperature`, `greenhouse_humidity`, `vpd`, `outside_temperature`, `outside_humidity`,
`control_room_temperature`, `control_room_humidity`, `gaht_input_temperature`, `gaht_input_humidity`,
`gaht_exit_temperature`, `gaht_exit_humidity`.

Targets (the live setpoints are the targets): `target_temp_low`, `target_temp_high`, `target_humidity`,
`alarm_cold`, `alarm_hot`.

Modes: `mode_switch` (the greenhouse mode), `gaht_state`.

Control: `thermostat`, `thermostat_reason`, `earth_battery_thermostat`, `earth_battery_reason`.

Equipment: `equipment_circulation_fans`, `equipment_north_fan`, `equipment_south_fan`, `equipment_evaporative_cooler`,
`equipment_shutters`, `equipment_gaht_fan`, `equipment_heater`, `equipment_mister`.

Other: `weather_forecast`.

Anything else is allowed; these are the ones the built-in checks look for.
