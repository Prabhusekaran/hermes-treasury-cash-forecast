"""Bridge a single canonical forecast line to existing ForecastModelSpec models."""
from .cash_flow_schema import (
    CashFlowSchema, ForecastRequest, LineForecast,
)


def forecast_line(schema: CashFlowSchema, request: ForecastRequest, *, model_specs=None):
    """One line, 12 months, training prefix only; no model additions or overlays.

    Mapping supplies recurring signed history. The compatibility view places
    each line amount in net_cash_flow with one_off_items=0, so existing models
    forecast the canonical line target rather than the old aggregate example.
    Imports/loaders are lazy; only the explicitly selected model is invoked.
    """
    if not isinstance(schema, CashFlowSchema) or not isinstance(request, ForecastRequest):
        raise ValueError("schema and request contracts required")
    # Revalidate boundaries, including externally constructed/mutated objects.
    schema.__post_init__()
    request.__post_init__()
    lines = {line.line_id: line for line in schema.lines}
    if request.line_id not in lines or lines[request.line_id].treatment != "forecast":
        raise ValueError("forecast request must reference a forecast treatment line")
    if model_specs is None:
        from .evaluate import MODEL_REGISTRY
        model_specs = MODEL_REGISTRY
    if request.model_name not in model_specs:
        raise ValueError("unknown forecast model")
    history = [{"month": row.month, "net_cash_flow": float(row.amount),
                "one_off_items": 0.0} for row in request.history]
    output = model_specs[request.model_name].forecast(history, request.horizon)
    return LineForecast(request.line_id, request.origin_month, request.model_name,
                        tuple(output["point"]), output["quantiles"])

