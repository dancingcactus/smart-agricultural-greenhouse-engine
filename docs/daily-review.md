# The daily review

Every morning in growing mode (and daily in charging mode) the Analyst role reviews the last 24 hours and the next 72.
The numbers come from the gateway in plain code, so the model reads results and writes the report; it does no arithmetic.

## Tools

- `run_cadence` (`GET /review/cadence`): the mode and which loops it allows. Every scheduled run calls it first and
  stops if its loop is not listed.
- `run_daily_review` (`GET /review/daily?hours=24`): the review's numbers. Live only; it answers 409 with an as-of time.

| Mode | Loops allowed |
|---|---|
| Growing | watch, daily, evening, weekly |
| Charging (Earth Battery) | daily, weekly (provisional: charging checks are not built yet, so the daily review skips the growing band) |
| Off | weekly (your install has no Off state today; an unrecognised mode falls back to growing and says so) |

## What `/review/daily` reports

| Section | Content | Raises a finding (amber) when |
|---|---|---|
| Watch findings | the hourly watch's results (stale, flat, alarms, thermostat faults) | listed first, with their own severity |
| Temperature | min, max, mean, share of time inside the band, hours below and above | in the band less than 80% of the window |
| Humidity | min, max, hours at or above 85%, and those inside 20:00 to 06:00 local | 2 or more night hours at or above 85% (Botrytis risk) |
| Equipment | runtime, number of runs and longest run for every approved `equipment_*` role | never; it is context |
| Forecast | coldest, hottest and windiest point in the next 72 hours (archived forecast, converted to °F and mph) | low at or under 34°F, high at or over 90°F, wind or gust at or over 30 mph |
| Gaps | roles not approved yet, and what is not measured (light, soil moisture, basket weight, ground temperature, pH/EC, fertilizer level, camera images) | |

`wake_daily_early` is true when the watch is not green, for the hourly automation to act on later.
The band is judged against today's setpoints, not the ones in force at each moment (the response says so).
The thresholds are constants at the top of `gateway/review.py`; they are starting points to tune.

## Not built yet

Evening check, weekly review and improvement review; water, nutrient and camera checks (no data yet); GAHT charging
checks; equipment commanded versus observed; comparing against a week earlier; scoring the agent's earlier predictions;
failure notifications and the dashboard "last report" time.

## Running it

In Open WebUI open **Automations** and create one:

- **Model:** SAGE Analyst (it has the gateway tool attached).
- **Schedule:** daily, in the morning.
- **Prompt:** the text of `context/prompts/automation_daily.md`.
- Use the run-now button once; the run should show `run_cadence` then `run_daily_review`.

Also update the hourly watch Automation's prompt from `context/prompts/automation_watch.md`, which now calls `run_cadence` first.
