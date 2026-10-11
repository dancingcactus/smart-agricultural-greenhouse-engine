"""Forecasting helpers for the agent: baselines, honest backtests, uncertainty bands, and prediction scoring.

Nothing here talks to Home Assistant or stores anything. Fetch evenly spaced history with
`greenhouse_gateway.Gateway.history_resampled` / `history_runtime`, forecast with any function you like (the
Chronos-2 and LightGBM wrappers below, or your own), prove it with `rolling_backtest`, then record the prediction
with `make_prediction` and score it later with `score_prediction`.

The rules from the requirements, in code:
  * every backtest fold sees only data before its cutoff (a time split, never a random one);
  * a forecaster is trusted only if it beats the "same hour yesterday" baseline on the rolling backtest.

Series are lists of [epoch_seconds, value] on an even grid, as the gateway returns them; value is None for a gap.
A forecaster is any function `forecaster(history, horizon, step) -> forecast`, where `history` is the series so
far, `horizon` the number of steps to predict and `step` the grid spacing in seconds. It returns either a list of
numbers or {"point": [...], "quantiles": {"0.1": [...], "0.9": [...]}}.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from itertools import pairwise

DAY = 86400
DEFAULT_LEVEL = 0.8


class ForecastError(ValueError):
    """The inputs cannot be forecast or scored as asked; the message says why."""


# --------------------------------------------------------------------------- series


def series_from(response, entity_id=None):
    """The points of one series out of a gateway `history_resampled` response (first series, or the named entity)."""
    items = response["series"]
    if entity_id is not None:
        items = [s for s in items if s["entity_id"] == entity_id]
    if not items:
        raise ForecastError(f"no series for {entity_id!r} in the response")
    return [[t, v] for t, v in items[0]["points"]]


def runtime_series(item):
    """One entry of a gateway `history_runtime` response as a daily series [day_start_epoch, hours_on].

    Days that are not complete (partial window or unknown state) become gaps rather than misleading low values.
    """
    from datetime import UTC, datetime
    out = []
    for day in item["days"]:
        t = datetime.fromisoformat(day["date"]).replace(tzinfo=UTC).timestamp()
        out.append([t, day["hours_on"] if day["complete"] else None])
    return out


def check_grid(points, step):
    """Raise unless the points are on one even grid of `step` seconds."""
    for (a, _), (b, _) in pairwise(points):
        if abs((b - a) - step) > 1e-6:
            raise ForecastError(f"series is not on a {step}s grid (gap between {a} and {b})")


def _known(value):
    return value is not None and isinstance(value, (int, float)) and math.isfinite(value)


def _mae(pairs):
    errs = [abs(a - f) for a, f in pairs if _known(a) and _known(f)]
    return sum(errs) / len(errs) if errs else None


# --------------------------------------------------------------------------- baseline


def baseline_forecast(history, horizon, step, season=None):
    """Seasonal naive: each future step repeats the value one season earlier (default: the same time yesterday).

    On a daily grid the season is one step, so this is "same as yesterday". When the horizon runs past a full
    season, the most recent known season is repeated. Gaps in the source are walked back one more season.
    """
    if not history:
        raise ForecastError("no history")
    if season is None:
        season = max(1, DAY // int(step))  # one day of steps; a daily grid gets 1, meaning "same as yesterday"
    values = [v for _, v in history]
    n = len(values)
    out = []
    for i in range(horizon):
        first = i // season + 1  # fewest whole seasons back that land inside the history
        found = None
        for k in range(first, first + 8):  # a gap in the source walks back one more season
            j = n + i - k * season
            if j < 0:
                break
            if _known(values[j]):
                found = values[j]
                break
        out.append(found)
    return out


baseline_yesterday_same_hour = baseline_forecast


# --------------------------------------------------------------------------- forecast shape


def _normalise(raw, horizon):
    if isinstance(raw, dict):
        point = list(raw["point"])
        quantiles = {str(k): list(v) for k, v in (raw.get("quantiles") or {}).items()}
    else:
        point, quantiles = list(raw), {}
    if len(point) != horizon or any(len(q) != horizon for q in quantiles.values()):
        raise ForecastError(f"forecaster returned {len(point)} steps, expected {horizon}")
    return {"point": point, "quantiles": quantiles}


def forecast_points(history, forecast, step):
    """Pair a forecast's values with the timestamps they are for: [[t, value], ...]."""
    last = history[-1][0]
    return [[last + (i + 1) * step, v] for i, v in enumerate(forecast["point"] if isinstance(forecast, dict) else forecast)]


# --------------------------------------------------------------------------- backtest


def skill(model_mae, baseline_mae):
    """1 - model/baseline error. Above 0 beats the baseline; 0.2 means 20% less error. None if undefined."""
    if model_mae is None or baseline_mae is None or baseline_mae <= 0:
        return None
    return 1 - model_mae / baseline_mae


def rolling_backtest(points, forecaster, horizon, step, folds=8, stride=None, min_history=None, baseline=None,
                     level=DEFAULT_LEVEL):
    """Score a forecaster against the baseline on rolling, strictly time-ordered folds.

    The last fold forecasts the final `horizon` steps; earlier folds step back `stride` steps (default `horizon`).
    Each fold's forecaster sees only `points[:cutoff]`, a copy, so nothing after the cutoff can leak in.

    Returns the model and baseline mean absolute error, `skill`, `win_rate` (share of folds the model won),
    `trusted`, per-fold detail, `residuals_by_lead` (for `bands_from_residuals`) and, when the forecaster gives
    quantiles, the share of actuals that fell inside the central `level` interval.
    """
    check_grid(points, step)
    stride = stride or horizon
    baseline = baseline or baseline_forecast
    min_history = min_history if min_history is not None else max(horizon, DAY // int(step) if step < DAY else 7) * 2
    lo_q, hi_q = str(round((1 - level) / 2, 4)), str(round(1 - (1 - level) / 2, 4))
    detail, residuals = [], [[] for _ in range(horizon)]
    all_model, all_base, hits, covered = [], [], 0, 0
    for k in range(folds):
        cut = len(points) - horizon - k * stride
        if cut < min_history:
            break
        history = [list(p) for p in points[:cut]]
        actual = [v for _, v in points[cut:cut + horizon]]
        forecast = _normalise(forecaster(history, horizon, step), horizon)
        base = baseline([list(p) for p in points[:cut]], horizon, step)
        model_pairs, base_pairs = list(zip(actual, forecast["point"], strict=True)), list(zip(actual, base, strict=True))
        m, b = _mae(model_pairs), _mae(base_pairs)
        for lead, (a, f) in enumerate(model_pairs):
            if _known(a) and _known(f):
                residuals[lead].append(a - f)
                all_model.append(abs(a - f))
        all_base.extend(abs(a - f) for a, f in base_pairs if _known(a) and _known(f))
        lo, hi = forecast["quantiles"].get(lo_q), forecast["quantiles"].get(hi_q)
        if lo and hi:
            for a, lq, hq in zip(actual, lo, hi, strict=True):
                if _known(a) and _known(lq) and _known(hq):
                    covered += 1
                    hits += lq <= a <= hq
        detail.append({"cutoff": points[cut][0], "model_mae": m, "baseline_mae": b,
                       "won": m is not None and b is not None and m < b})
    scored = [d for d in detail if d["model_mae"] is not None and d["baseline_mae"] is not None]
    model_mae = sum(all_model) / len(all_model) if all_model else None
    base_mae = sum(all_base) / len(all_base) if all_base else None
    s = skill(model_mae, base_mae)
    win_rate = sum(d["won"] for d in scored) / len(scored) if scored else None
    return {"folds": detail, "folds_scored": len(scored), "model_mae": model_mae, "baseline_mae": base_mae, "skill": s,
            "win_rate": win_rate, "trusted": trusted(s, win_rate, len(scored)), "residuals_by_lead": residuals,
            "interval_coverage": hits / covered if covered else None, "interval_level": level if covered else None}


def trusted(skill_value, win_rate, folds_scored, min_folds=3):
    """The model beat the baseline overall, won at least half the folds, and was tested on enough of them."""
    return bool(skill_value is not None and skill_value > 0 and folds_scored >= min_folds
                and win_rate is not None and win_rate >= 0.5)


# --------------------------------------------------------------------------- bands and anomalies


def _quantile(sorted_values, q):
    if not sorted_values:
        return None
    pos = q * (len(sorted_values) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def bands_from_residuals(point, residuals_by_lead, level=DEFAULT_LEVEL, min_samples=5):
    """Empirical uncertainty band around a point forecast: [[lo, hi], ...], from the backtest's own errors per lead.

    A step with fewer than `min_samples` past errors gets [None, None] rather than an invented band.
    """
    tail = (1 - level) / 2
    out = []
    for i, p in enumerate(point):
        res = sorted(residuals_by_lead[i]) if i < len(residuals_by_lead) else []
        if p is None or len(res) < min_samples:
            out.append([None, None])
        else:
            out.append([p + _quantile(res, tail), p + _quantile(res, 1 - tail)])
    return out


def flag_anomalies(actual, band_points):
    """Readings outside a band. `actual` is [[t, value]], `band_points` is [[t, lo, hi]]; returns the offenders."""
    by_time = {t: (lo, hi) for t, lo, hi in band_points}
    out = []
    for t, v in actual:
        lo, hi = by_time.get(t, (None, None))
        if not _known(v) or lo is None or hi is None:
            continue
        if v < lo or v > hi:
            out.append({"t": t, "value": v, "band": [lo, hi], "direction": "below" if v < lo else "above",
                        "distance": lo - v if v < lo else v - hi})
    return out


# --------------------------------------------------------------------------- forecasters


def baseline_forecaster(season=None):
    """The baseline as a forecaster, handy for sanity-checking the backtest itself."""
    return lambda history, horizon, step: baseline_forecast(history, horizon, step, season)


def _aligned(cov, stamps):
    """A covariate series looked up at the given timestamps as a float array, NaN where it has no value."""
    import numpy as np
    lookup = {t: v for t, v in cov}
    return np.array([lookup[t] if _known(lookup.get(t)) else np.nan for t in stamps], dtype=float)


def chronos2_forecaster(pipeline=None, model_path=None, quantiles=(0.1, 0.5, 0.9), covariates=None,
                        future_covariates=None, context_length=None):
    """A forecaster backed by Chronos-2 (CPU). The weights are baked into the sandbox image; nothing is downloaded.

    pipeline: a loaded `Chronos2Pipeline` (tests pass a stub); otherwise it is loaded once from `model_path`
        (default: the CHRONOS2_PATH environment variable, else "amazon/chronos-2" from the local cache).
    covariates: {name: series} of past values aligned to the same grid (for example outside temperature).
    future_covariates: {name: series} that also reach into the forecast period and are known ahead of time, such as
        a weather forecast. In a backtest, supply what was forecast at each cutoff, never the later actuals.
    Gaps (None) are passed on as missing values, which Chronos-2 handles.
    """
    state = {"pipeline": pipeline}
    levels = [float(q) for q in quantiles]

    def load():
        if state["pipeline"] is None:
            import os

            from chronos import Chronos2Pipeline
            path = model_path or os.environ.get("CHRONOS2_PATH") or "amazon/chronos-2"
            state["pipeline"] = Chronos2Pipeline.from_pretrained(path, device_map="cpu")
        return state["pipeline"]

    def run(history, horizon, step):
        import numpy as np
        stamps = [t for t, _ in history]
        future = [stamps[-1] + (i + 1) * step for i in range(horizon)]
        target = np.array([np.nan if not _known(v) else v for _, v in history], dtype=float)
        if context_length:
            stamps, target = stamps[-context_length:], target[-context_length:]
        item = {"target": target}
        for name, cov in (covariates or {}).items():
            item.setdefault("past_covariates", {})[name] = _aligned(cov, stamps)
        for name, cov in (future_covariates or {}).items():
            item.setdefault("past_covariates", {})[name] = _aligned(cov, stamps)
            item.setdefault("future_covariates", {})[name] = _aligned(cov, future)
        q, mean = load().predict_quantiles([item], prediction_length=horizon, quantile_levels=levels)
        grid = np.asarray(q[0])[0]  # (horizon, n_quantiles)
        return {"point": [float(x) for x in np.asarray(mean[0])[0]],
                "quantiles": {str(level): [float(x) for x in grid[:, i]] for i, level in enumerate(levels)}}

    return run


def lightgbm_runtime_forecaster(model_factory=None, extra_features=None, min_rows=14):
    """A forecaster for a daily series such as hours of GAHT fan runtime (grid of one day).

    Features per day: yesterday's value, the mean of the last 3 and last 7 days, and the weekday, plus
    `extra_features` ({name: series of the same grid, known for the target day, for example forecast max
    temperature}). Multi-day horizons feed each prediction back in. With fewer than `min_rows` usable days it
    falls back to the baseline rather than overfit.
    """
    def default_factory():
        import lightgbm as lgb
        return lgb.LGBMRegressor(n_estimators=150, learning_rate=0.05, num_leaves=7, min_child_samples=3, verbose=-1)

    factory = model_factory or default_factory

    def row(values, stamps_i, t, extra):
        recent = [v for v in values[stamps_i - 7:stamps_i] if _known(v)]
        last3 = [v for v in values[stamps_i - 3:stamps_i] if _known(v)]
        if not recent or not _known(values[stamps_i - 1]):
            return None
        wd = int((t // DAY + 3) % 7)  # epoch day 0 was a Thursday; 0 = Monday
        feats = [values[stamps_i - 1], sum(last3) / len(last3), sum(recent) / len(recent), wd]
        for name in sorted(extra):
            v = extra[name].get(t)
            if not _known(v):
                return None
            feats.append(v)
        return feats

    def run(history, horizon, step):
        if step != DAY:
            raise ForecastError("the LightGBM runtime forecaster needs a daily series")
        extra = {n: {t: v for t, v in s} for n, s in (extra_features or {}).items()}
        values = [v for _, v in history]
        xs, ys = [], []
        for i in range(7, len(history)):
            if not _known(values[i]):
                continue
            feats = row(values, i, history[i][0], extra)
            if feats is not None:
                xs.append(feats)
                ys.append(values[i])
        if len(xs) < min_rows:
            return baseline_forecast(history, horizon, step)
        model = factory()
        model.fit(xs, ys)
        stamps, work, out = [t for t, _ in history], list(values), []
        for i in range(horizon):
            t = stamps[-1] + (i + 1) * step
            feats = row(work, len(work), t, extra)
            if feats is None:  # cannot build features (missing covariate or no recent data): repeat the baseline
                pred = baseline_forecast([[s, v] for s, v in zip(stamps, work, strict=True)], 1, step)[0]
            else:
                pred = max(0.0, float(model.predict([feats])[0]))
            out.append(pred)
            stamps.append(t)
            work.append(pred)
        return out

    return run


# --------------------------------------------------------------------------- predictions


def make_prediction(target, window_start, window_end, value, interval=None, level=DEFAULT_LEVEL, rationale="",
                    made_at=None, evidence=None):
    """A falsifiable prediction as a plain dict for the agent's own log.

    target: what is predicted, for example "greenhouse_temperature max" or "equipment_gaht_fan hours_on".
    window_start/window_end: epoch seconds the prediction is about; it must end after `made_at`.
    interval: [low, high] the value should fall inside with probability `level`.
    evidence: anything that backs it, such as the backtest summary (skill, trusted) from `rolling_backtest`.
    """
    made_at = time.time() if made_at is None else made_at
    if window_end <= window_start:
        raise ForecastError("the window must end after it starts")
    if window_end <= made_at:
        raise ForecastError("the window is already over: a prediction about the past cannot be falsified")
    if interval is not None:
        if len(interval) != 2 or interval[0] > interval[1]:
            raise ForecastError("interval must be [low, high]")
        if not interval[0] <= value <= interval[1]:
            raise ForecastError("the value must lie inside its interval")
    body = {"target": target, "window_start": window_start, "window_end": window_end, "value": value,
            "interval": list(interval) if interval else None, "level": level if interval else None,
            "rationale": rationale, "made_at": made_at, "evidence": evidence}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:12]
    return {"id": digest, **body}


def score_prediction(prediction, actual):
    """Compare a prediction with what happened: error, whether the interval held, and the interval width."""
    if not _known(actual):
        raise ForecastError("actual must be a number")
    error = actual - prediction["value"]
    out = {"id": prediction["id"], "actual": actual, "error": error, "abs_error": abs(error), "pct_error": None,
           "inside_interval": None, "interval_width": None, "level": prediction.get("level")}
    if actual:
        out["pct_error"] = abs(error) / abs(actual) * 100
    if prediction.get("interval"):
        lo, hi = prediction["interval"]
        out["inside_interval"] = lo <= actual <= hi
        out["interval_width"] = hi - lo
    return out


def calibration(scores):
    """Summarise scored predictions: how big the errors are, which way they lean, and whether intervals held.

    `interval_coverage` should be close to the stated level (for example 0.8); well below it means the agent is
    overconfident, well above it means the intervals are wider than they need to be.
    """
    if not scores:
        return {"n": 0}
    with_interval = [s for s in scores if s["inside_interval"] is not None]
    levels = {s["level"] for s in with_interval}
    return {"n": len(scores), "mae": sum(s["abs_error"] for s in scores) / len(scores),
            "bias": sum(s["error"] for s in scores) / len(scores),
            "interval_coverage": sum(s["inside_interval"] for s in with_interval) / len(with_interval)
            if with_interval else None,
            "intervals": len(with_interval), "stated_level": levels.pop() if len(levels) == 1 else None}
