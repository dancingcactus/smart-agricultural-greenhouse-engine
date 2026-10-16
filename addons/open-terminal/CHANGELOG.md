# Changelog

All notable changes to the SAGE Sandbox add-on. Versions follow `version` in `config.yaml`.

## 0.2.5

- Fix: `lightgbm_runtime_forecaster()` failed with "scikit-learn is required" because it used LightGBM's scikit-learn wrapper, and the sandbox has no scikit-learn. It now uses LightGBM's own training interface, which needs only LightGBM and numpy. Found by the first live test of the LightGBM forecaster.

## 0.2.4

- The gateway client's `history_resampled()` and `history_runtime()` take a `source` ("auto", "vm" or "ha"). Needs SAGE Gateway 0.16.0 or later for months of history.

## 0.2.3

- Fix: the base image has no pip, so the forecasting models (Chronos-2, LightGBM, PyTorch) could not install (found from the 0.2.2 log: `No module named pip`). The build now bootstraps pip with `ensurepip` (falling back to get-pip.py), installs the models, then removes pip again so the sandbox stays as locked down as before.

## 0.2.2

- Fix: in 0.2.1 a failed model install exited before its error log was saved, so a failure looked the same as success in the add-on log. The steps now run in a subshell and the log is always kept on failure.

## 0.2.1

- A failure installing the forecasting models no longer stops the image from building (0.2.0 failed to build on a live install with no usable error). The reason is saved in the image and printed in the add-on log at start, and the gateway client and `greenhouse_forecast` baseline tools still work without the models.

## 0.2.0

- New `greenhouse_forecast` module: seasonal-naive baseline ("same hour yesterday"), rolling time-split backtest with skill and a trusted flag, forecast bands from backtest errors, anomaly flags, a Chronos-2 forecaster, a LightGBM forecaster for daily equipment runtime, and helpers to make and score predictions. It stores nothing.
- The image now includes Chronos-2 (weights downloaded at build time), LightGBM, PyTorch (CPU) and pandas, so forecasting works with no internet. TabPFN is not included until its weights license is checked for commercial use. This makes the image much larger; the first build takes a while. Not yet built or run: build it once and check the add-on starts before relying on it.
- The gateway client gains `history_resampled()` and `history_runtime()`. Needs SAGE Gateway 0.15.0 or later.

## 0.1.4

- The sandbox's gateway client gains `cadence()` and `daily_review()` for the daily review.

## 0.1.3

- The sandbox's gateway client gains `watch()` for the new health checks.

## 0.1.2

- The gateway client in the sandbox gains `roles()` for the new role lookup.

## 0.1.1

- Renamed to **SAGE Sandbox** (Smart Agricultural Greenhouse Engine). The add-on slug, options and data are unchanged.

## 0.1.0

- First version: Open Terminal with an egress firewall that allows only the SAGE Gateway (and any domains you list), no Home Assistant credentials, and a small client library for the gateway.
