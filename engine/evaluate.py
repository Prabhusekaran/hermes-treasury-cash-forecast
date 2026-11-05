"""Forecast accuracy measures and backtesting utilities for the 12-month framework.

The original single hold-out `backtest()` is preserved for compatibility.
The rolling-origin framework supports fair comparison of multiple forecasting
models on identical historical origins.

Run the legacy CLI with:  python -m treasury_lab.evaluate
"""

import math
from dataclasses import dataclass
from numbers import Real
from typing import Callable

from .probabilistic_metrics import probabilistic_summary
from .common import HISTORY_FILE, add_months, read_history
from .classical_models import ets_forecast, sarima_forecast
from .foundation_models import (
    CHRONOS_MIN_HISTORY, CHRONOS_MAX_HORIZON, chronos_2_forecast,
    TIREX_MIN_HISTORY, TIREX_MAX_HORIZON, tirex_2_forecast,
)
from .forecast import (
    MAX_HORIZON,
    MIN_HISTORY,
    check_monthly_sequence,
    fit_baseline,
    seasonal_naive_forecast,
    statistical_forecast,
    underlying_series,
)


def _check_inputs(actual, predicted):
    if len(actual) != len(predicted):
        raise ValueError("actual and predicted must have the same length")
    if len(actual) == 0:
        raise ValueError("actual and predicted must not be empty")


def mae(actual, predicted):
    _check_inputs(actual, predicted)
    return sum(abs(a - p) for a, p in zip(actual, predicted)) / len(actual)


def rmse(actual, predicted):
    _check_inputs(actual, predicted)
    return math.sqrt(sum((a - p) ** 2 for a, p in zip(actual, predicted)) / len(actual))


def mape_is_appropriate(actual, min_relative=0.1):
    """Return True only when MAPE is meaningful for these actuals."""
    if not actual:
        return False
    if not (all(a > 0 for a in actual) or all(a < 0 for a in actual)):
        return False
    average_size = sum(abs(a) for a in actual) / len(actual)
    return all(abs(a) >= min_relative * average_size for a in actual)


def mape(actual, predicted):
    """MAPE in percent, or None when MAPE is not appropriate."""
    _check_inputs(actual, predicted)
    if not mape_is_appropriate(actual):
        return None
    return 100 * sum(abs((a - p) / a) for a, p in zip(actual, predicted)) / len(actual)


def summarise(actual, predicted):
    return {
        "mae": mae(actual, predicted),
        "rmse": rmse(actual, predicted),
        "mape": mape(actual, predicted),
    }


def backtest(history, horizon=12):
    """Legacy single hold-out backtest. Kept unchanged for compatibility."""
    if horizon < 1 or len(history) - horizon < MIN_HISTORY:
        raise ValueError(
            f"Need at least {MIN_HISTORY} months of training data "
            f"after holding out {horizon} months"
        )

    train, test = history[:-horizon], history[-horizon:]
    actual = underlying_series(test)

    model = fit_baseline(train)
    baseline = [row["baseline_forecast"] for row in statistical_forecast(model, horizon)]
    naive = seasonal_naive_forecast(train, horizon)

    return {
        "horizon_months": horizon,
        "baseline": summarise(actual, baseline),
        "seasonal_naive": summarise(actual, naive),
    }


def previous_month_forecast(history, horizon):
    """Benchmark: repeat the last observed underlying cash flow."""
    if not isinstance(horizon, int) or isinstance(horizon, bool) or horizon < 1:
        raise ValueError("horizon must be a positive integer")
    if not history:
        raise ValueError("history must not be empty")
    last_value = underlying_series(history)[-1]
    return [last_value] * horizon


def seasonal_drift_forecast(history, horizon):
    """Benchmark: last year's seasonality plus the recent annual level change.

    The annual drift is the difference between the mean of the latest 12 months
    and the preceding 12 months. For horizons beyond 12 months, the same annual
    drift is added once per forecast year.
    """
    if len(history) < 24:
        raise ValueError("seasonal drift needs at least 24 months of history")
    if not isinstance(horizon, int) or isinstance(horizon, bool) or horizon < 1:
        raise ValueError("horizon must be a positive integer")

    values = underlying_series(history)
    previous_year = values[-24:-12]
    last_year = values[-12:]
    annual_drift = (
        sum(last_year) / 12
        - sum(previous_year) / 12
    )

    forecast = []
    for step in range(horizon):
        year_ahead = step // 12 + 1
        forecast.append(last_year[step % 12] + annual_drift * year_ahead)
    return forecast


@dataclass(frozen=True)
class ForecastModelSpec:
    """Small common interface used by the rolling evaluator."""

    name: str
    min_history: int
    max_horizon: int
    forecast_fn: Callable

    def forecast(self, history, horizon):
        if len(history) < self.min_history:
            raise ValueError(
                f"{self.name} needs at least {self.min_history} months of history"
            )
        if not 1 <= horizon <= self.max_horizon:
            raise ValueError(
                f"{self.name} horizon must be between 1 and {self.max_horizon}"
            )

        result = self.forecast_fn(history, horizon)
        if isinstance(result, dict):
            if "point" not in result:
                raise ValueError(f"{self.name} result is missing 'point'")
            point = result["point"]
            quantiles = result.get("quantiles", {})
        else:
            point = result
            quantiles = {}

        if len(point) != horizon:
            raise ValueError(f"{self.name} returned the wrong forecast length")
        point = list(point)
        for value in point:
            if not isinstance(value, Real) or isinstance(value, bool):
                raise ValueError(f"{self.name} point forecast must be numeric")
            if not math.isfinite(value):
                raise ValueError(f"{self.name} point forecast must be finite")

        if not isinstance(quantiles, dict):
            raise ValueError(f"{self.name} quantiles must be a dict")

        clean_quantiles = {}
        for q, values in quantiles.items():
            if not isinstance(q, Real) or isinstance(q, bool) or not 0 < q < 1:
                raise ValueError(
                    f"{self.name} quantile keys must be numeric and between 0 and 1"
                )
            values = list(values)
            if len(values) != horizon:
                raise ValueError(
                    f"{self.name} quantile {q} returned the wrong forecast length"
                )
            for value in values:
                if not isinstance(value, Real) or isinstance(value, bool):
                    raise ValueError(
                        f"{self.name} quantile {q} values must be numeric"
                    )
                if not math.isfinite(value):
                    raise ValueError(
                        f"{self.name} quantile {q} values must be finite"
                    )
            clean_quantiles[float(q)] = values

        ordered_quantiles = sorted(clean_quantiles)
        for step in range(horizon):
            previous = None
            for q in ordered_quantiles:
                value = clean_quantiles[q][step]
                if previous is not None and value < previous:
                    raise ValueError(
                        f"{self.name} quantiles cross at forecast step {step + 1}"
                    )
                previous = value

        return {"point": point, "quantiles": clean_quantiles}


def _baseline_point(history, horizon):
    model = fit_baseline(history)
    return [
        row["baseline_forecast"]
        for row in statistical_forecast(model, horizon)
    ]


MODEL_REGISTRY = {
    "tirex_2": ForecastModelSpec(
        "tirex_2", TIREX_MIN_HISTORY, TIREX_MAX_HORIZON, tirex_2_forecast
    ),
    "chronos_2": ForecastModelSpec(
        "chronos_2", CHRONOS_MIN_HISTORY, CHRONOS_MAX_HORIZON, chronos_2_forecast
    ),
    "baseline": ForecastModelSpec(
        "baseline", MIN_HISTORY, MAX_HORIZON, _baseline_point
    ),
    "seasonal_naive": ForecastModelSpec(
        "seasonal_naive", 12, MAX_HORIZON, seasonal_naive_forecast
    ),
    "seasonal_drift": ForecastModelSpec(
        "seasonal_drift", 24, MAX_HORIZON, seasonal_drift_forecast
    ),
    "previous_month": ForecastModelSpec(
        "previous_month", 1, MAX_HORIZON, previous_month_forecast
    ),
    "ets": ForecastModelSpec(
        "ets", 24, MAX_HORIZON, ets_forecast
    ),
    "sarima": ForecastModelSpec(
        "sarima", 36, MAX_HORIZON, sarima_forecast
    ),
}

DEFAULT_MODEL_NAMES = (
    "baseline",
    "seasonal_naive",
    "seasonal_drift",
    "previous_month",
)

SERIOUS_BENCHMARK_MODEL_NAMES = (
    "baseline",
    "seasonal_naive",
    "seasonal_drift",
    "previous_month",
    "ets",
    "sarima",
)


FOUNDATION_COMPARISON_MODEL_NAMES = SERIOUS_BENCHMARK_MODEL_NAMES + ("chronos_2", "tirex_2")


def common_min_history(model_names=DEFAULT_MODEL_NAMES, model_specs=None):
    """Minimum history required so every selected model can run."""
    registry = MODEL_REGISTRY if model_specs is None else model_specs
    return max(registry[name].min_history for name in model_names)


def _validate_history(history):
    """Validate the generic monthly history required by the current evaluator."""
    if not history:
        raise ValueError("history must not be empty")

    required = ("month", "net_cash_flow", "one_off_items")
    for index, row in enumerate(history):
        for key in required:
            if key not in row:
                raise ValueError(f"history row {index} is missing {key!r}")
        for key in ("net_cash_flow", "one_off_items"):
            value = row[key]
            if not isinstance(value, Real) or isinstance(value, bool):
                raise ValueError(f"history row {index} field {key!r} must be numeric")
            if not math.isfinite(value):
                raise ValueError(f"history row {index} field {key!r} must be finite")

    check_monthly_sequence(history)


def _validate_rolling_inputs(history, min_train, horizon, step):
    for name, value in (
        ("min_train", min_train),
        ("horizon", horizon),
        ("step", step),
    ):
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{name} must be an integer")

    if min_train < 1:
        raise ValueError("min_train must be at least 1")
    if not 1 <= horizon <= MAX_HORIZON:
        raise ValueError(f"horizon must be between 1 and {MAX_HORIZON}")
    if step <= 0:
        raise ValueError("step must be greater than 0")
    if len(history) < min_train + horizon:
        raise ValueError(
            f"Need at least {min_train + horizon} months of history "
            f"for min_train={min_train} and horizon={horizon}"
        )
    _validate_history(history)


def rolling_origins(history, min_train=MIN_HISTORY, horizon=12, step=3):
    """Return expanding-window training-end indices.

    Origins advance by `step`. If that cadence does not land on the latest
    possible valid origin, the latest origin is appended so the newest usable
    actual month is always evaluated.
    """
    _validate_rolling_inputs(history, min_train, horizon, step)
    latest_origin = len(history) - horizon
    origins = list(range(min_train, latest_origin + 1, step))
    if origins[-1] != latest_origin:
        origins.append(latest_origin)
    return origins


def _resolve_models(model_names, model_specs=None):
    registry = MODEL_REGISTRY if model_specs is None else model_specs
    names = tuple(model_names)
    if not names:
        raise ValueError("model_names must not be empty")
    unknown = [name for name in names if name not in registry]
    if unknown:
        raise ValueError(f"Unknown model(s): {', '.join(unknown)}")
    return names, registry


def _fold_forecasts(train, horizon, model_names, model_specs):
    """Generate model outputs using training data only."""
    return {
        name: model_specs[name].forecast(train, horizon)
        for name in model_names
    }


def _fold_score_rows(fold_index, origin_index, train, test, forecasts):
    """Create detailed month-level rows.

    Error sign convention: forecast minus actual. Positive means over-forecast.
    """
    origin_month = train[-1]["month"]
    total_actual = [row["net_cash_flow"] for row in test]
    underlying_actual = underlying_series(test)
    target_actuals = {
        "total_net_cash_flow": total_actual,
        "underlying_net_cash_flow": underlying_actual,
    }

    rows = []
    for model_name, model_output in forecasts.items():
        point = model_output["point"]
        quantiles = model_output["quantiles"]
        for target_name, actual in target_actuals.items():
            for month_ahead, (test_row, actual_value, forecast_value) in enumerate(
                zip(test, actual, point),
                start=1,
            ):
                rows.append({
                    "schema_version": 2,
                    "fold_index": fold_index,
                    "origin_index": origin_index,
                    "origin_month": origin_month,
                    "train_months": len(train),
                    "target_month": test_row["month"],
                    "month_ahead": month_ahead,
                    "model_name": model_name,
                    "target_name": target_name,
                    "actual": actual_value,
                    "point_forecast": forecast_value,
                    "quantiles": {
                        q: values[month_ahead - 1]
                        for q, values in quantiles.items()
                    },
                    "error": forecast_value - actual_value,
                    "absolute_error": abs(forecast_value - actual_value),
                    "actual_one_off": test_row["one_off_items"],
                    "is_one_off_month": test_row["one_off_items"] != 0.0,
                })
    return rows


def _fold_metrics(records):
    groups = {}
    for row in records:
        key = (row["fold_index"], row["model_name"], row["target_name"])
        groups.setdefault(key, []).append(row)

    results = []
    for (fold_index, model_name, target_name), rows in sorted(groups.items()):
        rows = sorted(rows, key=lambda row: row["month_ahead"])
        actual = [row["actual"] for row in rows]
        predicted = [row["point_forecast"] for row in rows]
        scores = summarise(actual, predicted)
        results.append({
            "fold_index": fold_index,
            "origin_month": rows[0]["origin_month"],
            "train_months": rows[0]["train_months"],
            "model_name": model_name,
            "target_name": target_name,
            **probabilistic_summary(rows),
            "mae": scores["mae"],
            "rmse": scores["rmse"],
            "mape": scores["mape"],
        })
    return results


def _aggregate_metric(values):
    """Median/min/max while treating None and non-finite values as unavailable."""
    available = sorted(
        value for value in values
        if value is not None and isinstance(value, (int, float))
        and not isinstance(value, bool) and math.isfinite(value)
    )
    unavailable = len(values) - len(available)
    if not available:
        return {
            "median": None,
            "min": None,
            "max": None,
            "available_folds": 0,
            "unavailable_folds": unavailable,
        }

    midpoint = len(available) // 2
    median = (
        available[midpoint]
        if len(available) % 2
        else (available[midpoint - 1] + available[midpoint]) / 2
    )
    return {
        "median": median,
        "min": available[0],
        "max": available[-1],
        "available_folds": len(available),
        "unavailable_folds": unavailable,
    }


def _aggregate_fold_metrics(fold_metrics):
    groups = {}
    for row in fold_metrics:
        key = (row["model_name"], row["target_name"])
        groups.setdefault(key, []).append(row)

    results = []
    for (model_name, target_name), rows in sorted(groups.items()):
        results.append({
            "model_name": model_name,
            "target_name": target_name,
            "fold_count": len(rows),
            **{key: _aggregate_metric([r[key] for r in rows])
               for key in ("pinball_q10", "pinball_q50", "pinball_q90",
                           "coverage_80", "interval_width_80", "interval_score_80")},
            "mae": _aggregate_metric([row["mae"] for row in rows]),
            "rmse": _aggregate_metric([row["rmse"] for row in rows]),
            "mape": _aggregate_metric([row["mape"] for row in rows]),
            "note": "Indicative only: neighbouring folds overlap and are correlated.",
        })
    return results


def _pooled_summary(rows):
    actual = [row["actual"] for row in rows]
    predicted = [row["point_forecast"] for row in rows]
    errors = [p - a for a, p in zip(actual, predicted)]
    abs_actual_total = sum(abs(a) for a in actual)
    return {
        **probabilistic_summary(rows),
        "n": len(rows),
        "distinct_target_months": len({row["target_month"] for row in rows}),
        "mae": mae(actual, predicted),
        "rmse": rmse(actual, predicted),
        "wape_pct": (
            None
            if abs_actual_total == 0
            else 100 * sum(abs(e) for e in errors) / abs_actual_total
        ),
        "bias": sum(errors) / len(errors),
        "bias_pct": (
            None
            if abs_actual_total == 0
            else 100 * sum(errors) / abs_actual_total
        ),
    }


def _pooled_metrics(records):
    """Pooled reporting by model, target and one-off split."""
    groups = {}
    for row in records:
        split = "one_off" if row["is_one_off_month"] else "non_one_off"
        for label in ("all", split):
            key = (row["model_name"], row["target_name"], label)
            groups.setdefault(key, []).append(row)

    results = []
    for (model_name, target_name, split), rows in sorted(groups.items()):
        summary = _pooled_summary(rows)
        results.append({
            "model_name": model_name,
            "target_name": target_name,
            "month_group": split,
            **summary,
        })

    seasonal_lookup = {
        (row["target_name"], row["month_group"]): row["wape_pct"]
        for row in results
        if row["model_name"] == "seasonal_naive"
    }
    for row in results:
        reference = seasonal_lookup.get((row["target_name"], row["month_group"]))
        if reference in (None, 0) or row["wape_pct"] is None:
            row["skill_vs_seasonal_naive_wape"] = None
        else:
            row["skill_vs_seasonal_naive_wape"] = (
                100 * (reference - row["wape_pct"]) / reference
            )
    return results


def _horizon_metrics(records):
    groups = {}
    for row in records:
        key = (row["model_name"], row["target_name"], row["month_ahead"])
        groups.setdefault(key, []).append(row)

    results = []
    for (model_name, target_name, month_ahead), rows in sorted(groups.items()):
        results.append({
            "model_name": model_name,
            "target_name": target_name,
            "month_ahead": month_ahead,
            **_pooled_summary(rows),
        })
    return results


def rolling_backtest(
    history,
    min_train=None,
    horizon=12,
    step=3,
    model_names=DEFAULT_MODEL_NAMES,
    model_specs=None,
):
    """Run expanding-window rolling-origin evaluation.

    Main target: total net cash flow, including actual historical one-offs.
    Diagnostic target: underlying net cash flow, with one-offs removed.

    Current point models forecast recurring cash flow only. Unknown future
    one-offs are implicitly forecast as zero. Known-event overlays remain
    excluded because current event records do not contain announcement dates.
    """
    names, registry = _resolve_models(model_names, model_specs=model_specs)
    required_min = common_min_history(names, model_specs=registry)
    if min_train is None:
        min_train = required_min
    if min_train < required_min:
        raise ValueError(
            f"min_train must be at least {required_min} months "
            f"for selected models: {', '.join(names)}"
        )

    origins = rolling_origins(
        history,
        min_train=min_train,
        horizon=horizon,
        step=step,
    )

    records = []
    for fold_index, origin_index in enumerate(origins, start=1):
        train = history[:origin_index]
        test = history[origin_index:origin_index + horizon]
        forecasts = _fold_forecasts(train, horizon, names, registry)
        records.extend(
            _fold_score_rows(
                fold_index,
                origin_index,
                train,
                test,
                forecasts,
            )
        )

    fold_metrics = _fold_metrics(records)
    return {
        "settings": {
            "schema_version": 2,
            "history_months": len(history),
            "min_train": min_train,
            "horizon": horizon,
            "step": step,
            "model_names": list(names),
            "fold_count": len(origins),
        },
        "records": records,
        "fold_metrics": fold_metrics,
        "aggregate": _aggregate_fold_metrics(fold_metrics),
        "pooled_metrics": _pooled_metrics(records),
        "horizon_metrics": _horizon_metrics(records),
    }


def main():
    if not HISTORY_FILE.exists():
        print("Synthetic data not found. Run first: python -m treasury_lab.generate_data")
        return 1

    result = backtest(read_history(), horizon=12)
    print(f"Backtest on the last {result['horizon_months']} months (EUR)")
    print(f"{'method':<18}{'MAE':>14}{'RMSE':>14}{'MAPE %':>10}")
    for name in ("baseline", "seasonal_naive"):
        scores = result[name]
        mape_text = "n/a" if scores["mape"] is None else f"{scores['mape']:.1f}"
        print(f"{name:<18}{scores['mae']:>14,.0f}{scores['rmse']:>14,.0f}{mape_text:>10}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

