# Resources

What the agent can read, how, and what each source cannot tell it. Every gateway query is clipped to the as-of time
(`X-As-Of`), so replayed episodes cannot see the future. This file starts with the forecasting resources; entries for
the other gateway routes (`/ha`, `/vm`, `/weather`, `/snapshot`, `/glossary`, `/review`, `/watch`) still need to be
added.

## Evenly spaced history: `GET /history/resampled`

- **What:** numeric history of one to five entities (or roles) on a fixed grid, for forecasting.
- **Parameters:** `entity_id` or `role` (exactly one), `step` (`5m` to `24h`, default `1h`), `start`, `end`.
- **Returns:** `series: [{entity_id, points: [[bucket_start_epoch, value_or_null], ...]}]`.
- **Parameters (also):** `source`: `auto` (default), `vm` or `ha`.
- **Sampling:** time-weighted mean per bucket (a reading holds until the next one). Only whole buckets inside the
  window are returned. `auto` reads VictoriaMetrics (months of history, up to 400 days) and falls back to Home
  Assistant's own history (about ten days) for an entity not recorded there. Each series says which `source` it came
  from, `series_merged` (stored series merged into it) and `first_sample`.
- **Gaps and quirks:** a bucket with no numeric reading is `null`, never filled in. From Home Assistant,
  `unavailable`, `unknown` and other non-numbers count as no reading; VictoriaMetrics records none of those, so a
  sensor that stopped reporting holds its last value there. Entities can have two stored series (a label change on
  2026-03-08 for the greenhouse temperature and humidity); they are merged by time. At most 20,000 points.
- **Units:** whatever the entity reports; the endpoint does not convert.
- **Cannot tell you:** why a value changed, what the setpoint was at the time, or anything about entities without
  recorded history.

## Equipment runtime per day: `GET /history/runtime`

- **What:** hours switched on per local day for equipment such as `equipment_gaht_fan`.
- **Parameters:** `entity_id` or `role`, `days` (1 to 400, or 93 with `source=ha`; default 14), `end`, `timezone` (default: Home Assistant's), `source`.
- **Returns:** `series: [{entity_id, days: [{date, hours_on, runs, known_hours, complete}]}]`.
- **Gaps and quirks:** `complete` is false for a day the window only partly covers, or where the state was unknown
  for more than 15 minutes. Treat incomplete days as missing, not as low runtime. States counted as on: on, open,
  opening, heat, heating, cool, cooling, running, fan_only.
- **Cannot tell you:** whether the equipment was commanded or only observed, or how much it moved (no flow data).

## Modeling tools (sandbox, CPU, no internet)

- **`greenhouse_forecast`** (a Python module in the sandbox; `help(greenhouse_forecast)` lists everything): baseline,
  rolling time-split backtest, skill and the `trusted` flag, forecast bands from backtest errors, anomaly flags,
  Chronos-2 and LightGBM forecasters, `make_prediction` / `score_prediction` / `calibration`. Pure Python except the
  two model wrappers. It stores nothing; the agent keeps its own prediction log in `~/predictions.jsonl`.
- **Chronos-2** (`amazon/chronos-2`, weights baked into the image at `$CHRONOS2_PATH`): continuous series, point
  forecast and quantiles, optional covariates.
- **LightGBM:** daily tables such as equipment runtime. Falls back to the baseline with fewer than 14 usable days.
- **TabPFN:** not installed. Its weights license must be checked for commercial use first.
- **Rule:** a model is trusted only if it beats "same hour yesterday" on a rolling backtest that respects the as-of
  cutoff. What-if predictions are trusted only inside the range of settings seen in the training data.
