"""Classical monthly forecasting models used as serious benchmarks.

These models forecast the same underlying recurring cash-flow series used by
the transparent benchmark models in evaluate.py. They never receive the
held-out test window.

Dependencies: statsmodels
"""

from .forecast import MAX_HORIZON, underlying_series


def ets_forecast(history, horizon):
    """Holt-Winters exponential smoothing with additive trend and seasonality."""
    try:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
    except ImportError as exc:
        raise ImportError(
            "ETS benchmark requires statsmodels. Install with: pip install statsmodels"
        ) from exc

    if len(history) < 24:
        raise ValueError("ETS needs at least 24 months of history")
    if not 1 <= horizon <= MAX_HORIZON:
        raise ValueError(f"horizon must be between 1 and {MAX_HORIZON}")

    values = underlying_series(history)
    model = ExponentialSmoothing(
        values,
        trend="add",
        seasonal="add",
        seasonal_periods=12,
        initialization_method="estimated",
    )
    fitted = model.fit(optimized=True, remove_bias=False)
    return [float(value) for value in fitted.forecast(horizon)]


def sarima_forecast(history, horizon):
    """Fixed seasonal ARIMA benchmark for monthly cash flow.

    Specification:
        SARIMA(1,1,0) x (0,1,1,12)

    The order is intentionally fixed rather than auto-tuned so every rolling
    fold is evaluated under the same modelling rule.
    """
    try:
        from statsmodels.tsa.statespace.sarimax import SARIMAX
    except ImportError as exc:
        raise ImportError(
            "SARIMA benchmark requires statsmodels. Install with: pip install statsmodels"
        ) from exc

    if len(history) < 36:
        raise ValueError("SARIMA needs at least 36 months of history")
    if not 1 <= horizon <= MAX_HORIZON:
        raise ValueError(f"horizon must be between 1 and {MAX_HORIZON}")

    values = underlying_series(history)
    model = SARIMAX(
        values,
        order=(1, 1, 0),
        seasonal_order=(0, 1, 1, 12),
        trend="n",
        enforce_stationarity=False,
        enforce_invertibility=False,
    )
    fitted = model.fit(disp=False, maxiter=200)
    return [float(value) for value in fitted.forecast(steps=horizon)]
