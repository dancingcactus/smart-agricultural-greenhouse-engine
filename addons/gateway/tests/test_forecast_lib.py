import importlib.util
import math
from pathlib import Path

import pytest

SDK = Path(__file__).resolve().parents[3] / "addons/open-terminal/sdk/greenhouse_forecast.py"
spec = importlib.util.spec_from_file_location("greenhouse_forecast", SDK)
gf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gf)

T0 = 1_800_000_000.0 - 1_800_000_000.0 % 86400
H, D = 3600, 86400


def truth(k):
    """Hourly index k: a daily cycle plus a slow upward drift."""
    return 60 + 10 * math.sin(2 * math.pi * k / 24) + 0.05 * k


def hourly(n, fn=truth):
    return [[T0 + k * H, fn(k)] for k in range(n)]


def oracle(history, horizon, step):
    n = len(history)
    return [truth(n + i) for i in range(horizon)]


def test_baseline_repeats_yesterday_and_the_latest_known_day_beyond_it():
    pts = hourly(72)
    out = gf.baseline_forecast(pts, 30, H)
    assert out[:24] == [p[1] for p in pts[48:72]]
    assert out[24:30] == [p[1] for p in pts[48:54]]  # beyond 24h: still the last known day, not made-up values


def test_baseline_walks_back_over_gaps_and_gives_none_when_nothing_is_known():
    pts = hourly(72)
    pts[60][1] = None  # the value one day before forecast step 12
    assert gf.baseline_forecast(pts, 13, H)[12] == pts[36][1]
    assert gf.baseline_forecast(hourly(10), 3, H) == [None, None, None]
    daily = [[T0 + i * D, v] for i, v in enumerate([5.0, 6.0, 7.0])]
    assert gf.baseline_forecast(daily, 2, D) == [7.0, 7.0]  # on a daily grid, same as yesterday


def test_check_grid_rejects_uneven_series():
    with pytest.raises(gf.ForecastError):
        gf.check_grid([[0, 1], [3600, 1], [9000, 1]], 3600)


def test_backtest_folds_only_see_data_before_their_cutoff():
    pts = hourly(24 * 14)
    seen = []

    def spy(history, horizon, step):
        seen.append((history[-1][0], len(history)))
        history[0][1] = -999  # a forecaster scribbling on its input must not touch the caller's series
        return [0.0] * horizon

    out = gf.rolling_backtest(pts, spy, horizon=24, step=H, folds=5)
    cutoffs = [f["cutoff"] for f in out["folds"]]
    assert len(seen) == 5 and cutoffs == sorted(cutoffs, reverse=True)
    for (last_seen, _), cutoff in zip(seen, cutoffs, strict=True):
        assert last_seen < cutoff  # nothing at or after the cutoff was visible
    assert pts[0][1] == truth(0) and cutoffs[0] == pts[-24][0]


def test_a_model_that_beats_the_baseline_is_trusted_and_one_that_does_not_is_not():
    pts = hourly(24 * 20)
    good = gf.rolling_backtest(pts, oracle, 24, H, folds=6)
    assert good["skill"] == pytest.approx(1.0) and good["trusted"] and good["win_rate"] == 1.0
    same = gf.rolling_backtest(pts, gf.baseline_forecaster(), 24, H, folds=6)
    assert same["skill"] == pytest.approx(0.0) and not same["trusted"]
    bad = gf.rolling_backtest(pts, lambda h, n, s: [0.0] * n, 24, H, folds=6)
    assert bad["skill"] < 0 and not bad["trusted"]


def test_too_few_folds_is_not_trusted():
    pts = hourly(24 * 4)  # history for only two folds
    out = gf.rolling_backtest(pts, oracle, 24, H, folds=6)
    assert out["folds_scored"] == 2 and not out["trusted"]


def test_backtest_skips_gaps_and_reports_interval_coverage():
    pts = hourly(24 * 14)
    pts[-5][1] = None

    def banded(history, horizon, step):
        point = oracle(history, horizon, step)
        return {"point": point, "quantiles": {"0.1": [p - 1 for p in point], "0.9": [p + 1 for p in point]}}

    out = gf.rolling_backtest(pts, banded, 24, H, folds=4)
    assert out["model_mae"] == pytest.approx(0.0, abs=1e-9) and out["interval_coverage"] == 1.0
    assert out["interval_level"] == 0.8


def test_wrong_length_forecast_is_an_error():
    with pytest.raises(gf.ForecastError):
        gf.rolling_backtest(hourly(24 * 10), lambda h, n, s: [1.0], 24, H, folds=2)


def test_bands_from_residuals_and_anomalies():
    residuals = [[-2.0, -1.0, 0.0, 1.0, 2.0] * 2, []]
    bands = gf.bands_from_residuals([50.0, 50.0], residuals, level=0.8)
    assert bands[0][0] < 50 < bands[0][1] and bands[1] == [None, None]  # no past errors, no invented band
    flags = gf.flag_anomalies([[1, 52.0], [2, 40.0], [3, 50.0], [4, None], [5, 99.0]],
                              [[1, 48, 52.5], [2, 48, 52.5], [3, 48, 52.5], [4, 48, 52.5]])
    assert [(f["t"], f["direction"]) for f in flags] == [(2, "below")] and flags[0]["distance"] == 8


class StubChronos:
    def __init__(self):
        self.calls = []

    def predict_quantiles(self, inputs, prediction_length, quantile_levels):
        import numpy as np
        self.calls.append((inputs, prediction_length, quantile_levels))
        base = np.arange(prediction_length, dtype=float)
        q = np.stack([base - 1, base, base + 1], axis=-1)[None]  # (variates, horizon, quantiles)
        return [q], [base[None]]


def test_chronos_wrapper_builds_inputs_and_shapes_the_output():
    np = pytest.importorskip("numpy")
    stub = StubChronos()
    pts = hourly(48)
    pts[10][1] = None
    outside = [[T0 + k * H, 40.0 + k] for k in range(48 + 6)]  # known into the future, like a weather forecast
    f = gf.chronos2_forecaster(pipeline=stub, future_covariates={"outside": outside})
    out = f(pts, 6, H)
    (inputs, horizon, levels) = stub.calls[0]
    item = inputs[0]
    assert horizon == 6 and levels == [0.1, 0.5, 0.9] and len(item["target"]) == 48 and np.isnan(item["target"][10])
    assert list(item["future_covariates"]["outside"]) == [40.0 + k for k in range(48, 54)]
    assert len(item["past_covariates"]["outside"]) == 48
    assert out["point"] == [0, 1, 2, 3, 4, 5] and out["quantiles"]["0.1"] == [-1, 0, 1, 2, 3, 4]
    assert gf.rolling_backtest(hourly(24 * 8), f, 24, H, folds=2)["folds_scored"] == 2  # plugs into the backtest


class MeanModel:
    def fit(self, xs, ys):
        self.value = sum(ys) / len(ys)

    def predict(self, xs):
        return [self.value] * len(xs)


def test_lightgbm_runtime_forecaster_uses_the_model_and_falls_back_when_data_is_short():
    pytest.importorskip("numpy")
    days = [[T0 + i * D, 4.0 + (i % 7)] for i in range(40)]
    f = gf.lightgbm_runtime_forecaster(model_factory=MeanModel)
    out = f(days, 3, D)
    assert len(out) == 3 and all(abs(v - out[0]) < 2 for v in out) and all(v >= 0 for v in out)
    short = [[T0 + i * D, 5.0] for i in range(6)]
    assert gf.lightgbm_runtime_forecaster(model_factory=MeanModel)(short, 2, D) == [5.0, 5.0]  # baseline
    with pytest.raises(gf.ForecastError):
        f(hourly(100), 3, H)


def test_default_lightgbm_model_needs_no_scikit_learn():
    pytest.importorskip("lightgbm")
    pytest.importorskip("numpy")
    days = [[T0 + i * D, 4.0 + (i % 7) + 0.1 * i] for i in range(60)]
    out = gf.lightgbm_runtime_forecaster()(days, 3, D)  # the default model, not a stand-in
    assert len(out) == 3 and all(v >= 0 for v in out) and abs(out[0] - (4.0 + (60 % 7) + 6.0)) < 2
    result = gf.rolling_backtest(days, gf.lightgbm_runtime_forecaster(), 1, D, folds=14, min_history=21)
    assert result["folds_scored"] == 14 and result["skill"] > 0


def test_runtime_series_and_series_from():
    item = {"days": [{"date": "2026-10-01", "hours_on": 6.0, "complete": True},
                     {"date": "2026-10-02", "hours_on": 1.0, "complete": False}]}
    out = gf.runtime_series(item)
    assert out[0][1] == 6.0 and out[1][1] is None and out[1][0] - out[0][0] == D  # partial day is a gap
    resp = {"series": [{"entity_id": "a.b", "points": [[1, 2.0]]}, {"entity_id": "c.d", "points": [[3, 4.0]]}]}
    assert gf.series_from(resp) == [[1, 2.0]] and gf.series_from(resp, "c.d") == [[3, 4.0]]
    with pytest.raises(gf.ForecastError):
        gf.series_from(resp, "x.y")


def test_predictions_are_falsifiable_and_scored():
    p = gf.make_prediction("gh temp max", T0 + 10 * H, T0 + 20 * H, 88.0, interval=[84, 92], rationale="clear sky",
                           made_at=T0)
    assert p["id"] and p["interval"] == [84, 92] and p["level"] == 0.8
    assert gf.make_prediction("gh temp max", T0 + 10 * H, T0 + 20 * H, 88.0, interval=[84, 92], rationale="clear sky",
                              made_at=T0)["id"] == p["id"]  # same claim, same id
    with pytest.raises(gf.ForecastError):
        gf.make_prediction("x", T0 - 2 * H, T0 - H, 1.0, made_at=T0)  # about the past
    with pytest.raises(gf.ForecastError):
        gf.make_prediction("x", T0, T0 + H, 1.0, interval=[2, 3], made_at=T0 - 1)  # value outside interval
    hit = gf.score_prediction(p, 90.0)
    miss = gf.score_prediction(p, 80.0)
    assert hit["inside_interval"] and hit["error"] == 2.0 and miss["inside_interval"] is False and miss["error"] == -8.0
    summary = gf.calibration([hit, miss])
    assert summary["n"] == 2 and summary["interval_coverage"] == 0.5 and summary["bias"] == -3.0
    assert summary["mae"] == 5.0 and summary["stated_level"] == 0.8 and gf.calibration([]) == {"n": 0}
