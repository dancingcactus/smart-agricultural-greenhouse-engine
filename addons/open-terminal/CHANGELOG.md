# Changelog

All notable changes to the SAGE Sandbox add-on. Versions follow `version` in `config.yaml`.

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
