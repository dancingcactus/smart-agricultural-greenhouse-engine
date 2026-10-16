# Forecasting and predictions

The gateway serves clean data; the agent makes the forecasts and keeps its own predictions. Nothing in the gateway
predicts or stores a prediction, so it stays read-only.

## The flow

1. **Get data.** In the sandbox:
   ```python
   from greenhouse_gateway import Gateway
   import greenhouse_forecast as gf
   gw = Gateway()
   resp = gw.history_resampled(role="greenhouse_temperature", step="1h", start="2026-09-01T00:00:00Z")
   temp = gf.series_from(resp)
   fan = gf.runtime_series(gw.history_runtime(role="equipment_gaht_fan", days=60)["series"][0])
   ```
2. **Forecast.** Any function `forecaster(history, horizon, step)` works. Ready-made:
   `gf.chronos2_forecaster(...)` (continuous series, with quantiles and optional covariates),
   `gf.lightgbm_runtime_forecaster()` (daily runtime), `gf.baseline_forecaster()`.
3. **Prove it.** `gf.rolling_backtest(temp, forecaster, horizon=24, step=3600, folds=8)`. Every fold sees only data
   before its cutoff. The result has `skill` against "same hour yesterday" (above 0 beats it), `win_rate`,
   `interval_coverage`, and `trusted`. If `trusted` is false, say so and use the baseline.
4. **Bands.** `gf.bands_from_residuals(point, result["residuals_by_lead"])` gives an empirical interval from the
   backtest's own errors; `gf.flag_anomalies(actual, band_points)` lists readings outside it.
5. **Record.** `p = gf.make_prediction("gh temp max", start, end, 88.0, interval=[84, 92], rationale="...",
   evidence={"skill": r["skill"], "trusted": r["trusted"]})`, then append `json.dumps(p)` to `~/predictions.jsonl`
   (the sandbox home folder is in add-on storage, so backups include it). A prediction about the past is refused.
6. **Score.** When the window is over, fetch what happened and call `gf.score_prediction(p, actual)`; summarise with
   `gf.calibration(scores)`. Intervals that hold far less often than their stated level mean the agent is
   overconfident.

## Writing your own forecaster

A forecaster is `f(history, horizon, step)`. `history` is `[[epoch, value_or_None], ...]` on an even grid up to the
cutoff (a copy: changing it is harmless). Return a list of `horizon` numbers, or
`{"point": [...], "quantiles": {"0.1": [...], "0.9": [...]}}` to get interval coverage in the backtest. Use only
`history`; anything else that is known later (actuals, a later weather forecast) leaks the future. If you use weather
as an input in a backtest, use the forecast that was archived at each cutoff (`weather_forecast` with an as-of time),
not the observations. Pass it to `rolling_backtest` like any other forecaster.

## Rules

- Time split only, never random. Respect the as-of cutoff.
- Trust a model only if it beats the baseline on the rolling backtest (`trusted`).
- What-if predictions only inside the range of settings seen in the training data.
- Web lookups go through Open WebUI's web search, not the sandbox, which has no internet. Treat what comes back as
  leads to check.

## What is not here yet

TabPFN (its weights license is unchecked), the Chronos-2 fine-tuned adapters from the requirements, a chart of
forecast bands, and the `decide` tool. The Dockerfile build with the real model weights has not been run.
