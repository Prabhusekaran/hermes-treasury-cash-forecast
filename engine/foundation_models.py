"""Optional foundation-model adapters for recurring monthly cash flow.

Chronos, TiRex and torch are imported only when a real forecast is requested.
"""
import math
import os
from functools import lru_cache
from numbers import Real

from .forecast import check_monthly_sequence, underlying_series

CHRONOS_MIN_HISTORY = 1
CHRONOS_MAX_HORIZON = 12
QUANTILE_LEVELS = (0.1, 0.5, 0.9)
TIREX_MIN_HISTORY = 1
TIREX_MAX_HORIZON = 12


@lru_cache(maxsize=1)
def _load_tirex_model():
    """Lazy, offline CPU loader; no implicit download or Docker fallback."""
    try:
        from tirex2 import load_model
    except ImportError as exc:
        raise ImportError("TiRex-2 requires the optional tirex-2 package; "
                          "see docs/tirex_2.md for Windows constraints.") from exc
    return load_model(os.environ.get("TIREX_MODEL_PATH", "NX-AI/TiRex-2"),
                      device="cpu", compile=False,
                      hf_kwargs={"local_files_only": True})


def _make_tirex_input(values):
    from tirex2 import TimeseriesType
    return TimeseriesType(target=_make_input(values).unsqueeze(0),
                          past_covariates=None, future_covariates=None)


def tirex_2_forecast(history, horizon, *, model=None):
    """Forecast recurring flow from the training slice alone, with point=q50.

    Official TiRex-2 output axes are (variate, nine quantiles, horizon).
    No covariates, fitting or mutable streaming state are used across folds.
    """
    if not history:
        raise ValueError("TiRex-2 needs at least one month of history")
    if (not isinstance(horizon, int) or isinstance(horizon, bool)
            or not 1 <= horizon <= TIREX_MAX_HORIZON):
        raise ValueError(f"horizon must be between 1 and {TIREX_MAX_HORIZON}")
    check_monthly_sequence(history)
    for row in history:
        for key in ("net_cash_flow", "one_off_items"):
            value = row[key]
            if (not isinstance(value, Real) or isinstance(value, bool)
                    or not math.isfinite(value)):
                raise ValueError("TiRex-2 training values must be finite numeric values")
    values = underlying_series(history)
    if not all(math.isfinite(v) for v in values):
        raise ValueError("TiRex-2 underlying training values must be finite")
    inputs = _make_tirex_input(values)
    if not all(math.isfinite(v) for v in inputs.target.tolist()[0]):
        raise ValueError("TiRex-2 tensor training values must be finite")
    if model is None:
        model = _load_tirex_model()
    levels = getattr(model, "quantiles", None)
    if levels is None:
        raise ValueError("TiRex-2 native quantile levels are unavailable")
    if hasattr(levels, "detach"):
        levels = levels.detach().cpu().tolist()
    if len(levels) != 9 or any(not math.isclose(float(q), (i + 1) / 10,
                                              rel_tol=0, abs_tol=1e-6)
                               for i, q in enumerate(levels)):
        raise ValueError("TiRex-2 returned unexpected native quantile levels")
    batches = model.forecast([inputs], prediction_length=horizon, output_type="torch")
    if len(batches) != 1:
        raise ValueError("TiRex-2 expected one forecast batch")
    batch = batches[0]
    if tuple(batch.shape) != (1, 9, horizon):
        raise ValueError("TiRex-2 returned the wrong quantile shape")
    rows = batch.detach().cpu().tolist()[0]
    if not all(math.isfinite(v) for series in rows for v in series):
        raise ValueError("TiRex-2 quantiles must be finite")
    crossings = [{"month_ahead": t + 1, "lower_index": i,
                  "upper_index": i + 1, "gap_eur": rows[i][t] - rows[i + 1][t]}
                 for t in range(horizon) for i in range(8)
                 if rows[i][t] > rows[i + 1][t]]
    quantiles = {q: list(map(float, rows[i]))
                 for q, i in zip(QUANTILE_LEVELS, (0, 4, 8))}
    if any(not quantiles[0.1][t] <= quantiles[0.5][t] <= quantiles[0.9][t]
           for t in range(horizon)):
        raise ValueError("TiRex-2 published quantiles cross")
    return {"point": list(quantiles[0.5]), "quantiles": quantiles,
            "native_diagnostics": {
                "native_quantile_crossing_count": len(crossings),
                "max_native_crossing_eur": max((c["gap_eur"] for c in crossings), default=0.0),
                "affected_horizons": sorted({c["month_ahead"] for c in crossings}),
                "crossings": crossings}}


@lru_cache(maxsize=1)
def _load_chronos_pipeline():
    """Reuse immutable pretrained weights across folds; never fit on history."""
    try:
        from chronos import Chronos2Pipeline
    except ImportError as exc:
        raise ImportError(
            "Chronos-2 requires the optional chronos-forecasting package "
            "in your chronos-treasury environment."
        ) from exc
    return Chronos2Pipeline.from_pretrained(
        "amazon/chronos-2", device_map="cpu", local_files_only=True
    )


def _make_input(values):
    import torch
    return torch.tensor(values, dtype=torch.float32)


def chronos_2_forecast(history, horizon, *, pipeline=None):
    """Return point=q50 and {0.1, 0.5, 0.9} in the evaluator schema.

    Only the supplied training slice is used. Historical one-offs are removed;
    no future covariates, known-event overlays or fine-tuning are supplied.
    An injected pipeline permits model-free tests.
    """
    if not history:
        raise ValueError("Chronos-2 needs at least one month of history")
    if (not isinstance(horizon, int) or isinstance(horizon, bool)
            or not 1 <= horizon <= CHRONOS_MAX_HORIZON):
        raise ValueError(f"horizon must be between 1 and {CHRONOS_MAX_HORIZON}")
    check_monthly_sequence(history)
    for row in history:
        for key in ("net_cash_flow", "one_off_items"):
            value = row[key]
            if (not isinstance(value, Real) or isinstance(value, bool)
                    or not math.isfinite(value)):
                raise ValueError("Chronos-2 training values must be finite numeric values")
    values = underlying_series(history)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Chronos-2 underlying training values must be finite")
    inputs = _make_input(values)
    # float32 conversion can overflow even when the original Python floats are finite.
    input_values = inputs.tolist()
    if not all(math.isfinite(value) for value in input_values):
        raise ValueError("Chronos-2 tensor training values must be finite")
    if pipeline is None:
        pipeline = _load_chronos_pipeline()
    quantile_batches, _ = pipeline.predict_quantiles(
        inputs=[inputs], prediction_length=horizon,
        quantile_levels=list(QUANTILE_LEVELS),
    )
    if len(quantile_batches) != 1:
        raise ValueError("Chronos-2 expected one forecast batch")
    batch = quantile_batches[0]
    # API shape: (n_variates=1, prediction_length, n_quantiles=3).
    if tuple(batch.shape) != (1, horizon, len(QUANTILE_LEVELS)):
        raise ValueError("Chronos-2 returned the wrong quantile shape")
    rows = batch.detach().cpu().tolist()[0]
    quantiles = {q: [float(row[i]) for row in rows]
                 for i, q in enumerate(QUANTILE_LEVELS)}
    if not all(math.isfinite(v) for series in quantiles.values() for v in series):
        raise ValueError("Chronos-2 quantiles must be finite")
    if any(row[0] > row[1] or row[1] > row[2] for row in rows):
        raise ValueError("Chronos-2 quantiles cross")
    # The API's second return value can be a mean. Explicit q50 keeps the
    # point forecast consistently median-based across package versions.
    return {"point": list(quantiles[0.5]), "quantiles": quantiles}

