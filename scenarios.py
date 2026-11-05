"""Run the base case and the scenarios from one set of model forecasts.

The models run once. Everything after that is assembly by the engine:

1. Base case. On a forecast line, a known amount marked "replace" takes the
   place of the model figure for that month (the model figure is set to zero and
   the known amount is added). A known amount marked "on top" is added to it.
   When the replaced months are the first months of the forecast, the model
   continues from their level: the later months keep the model's own pattern
   (trend and seasonality) but are moved by the difference between the last
   known amount and the model's figure for that month. The line therefore does
   not fall back to the level it had before the known amounts. (Fitting the
   model again with the known amounts as extra data points was tried and
   rejected: a two-month step was read as a much steeper trend.)
2. Scenarios. A percentage is converted to an amount using the base-case figure
   of that line and month (model forecast plus known schedule). Fixed amounts
   are used as given. The engine then assembles the scenario on the same base.

The untouched model forecasts are kept in the audit file next to the list of
months where a known amount replaced them.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, ROUND_HALF_EVEN

from .engine.cash_flow import assemble_cash_flow
from .engine.cash_flow_orchestration import run_cash_flow
from .engine.cash_flow_schema import HORIZON, LineForecast, ManagementAssumption
from .engine.common import add_months

CENT = Decimal("0.01")
BASE = "base"


def _month_text(month):
    return f"{month.year:04d}-{month.month:02d}"


def _replaced_months(inputs):
    """(line_id, month index 0..11) pairs where a known amount replaces the model figure."""
    targets = {add_months(inputs.origin_month, i + 1): i for i in range(HORIZON)}
    pairs = set()
    for event in inputs.known_events:
        if (event.event_id in inputs.replace_ids and event.effective_month in targets
                and event.known_from_month <= inputs.origin_month):
            pairs.add((event.line_id, targets[event.effective_month]))
    return pairs


def _known_amount(inputs, line_id, month):
    return sum(float(e.amount) for e in inputs.known_events
               if e.line_id == line_id and e.effective_month == month
               and e.known_from_month <= inputs.origin_month)


def _continue_from_known(inputs, forecast, lead):
    """Keep the model's pattern after `lead` known months, moved to the level of the last one."""
    last = lead - 1
    known = _known_amount(inputs, forecast.line_id, add_months(inputs.origin_month, lead))
    shift = known - forecast.point[last]
    point = (0.0,) * lead + tuple(v + shift for v in forecast.point[lead:])
    quantiles = {q: (0.0,) * lead + tuple(v + shift for v in values[lead:])
                 for q, values in forecast.quantiles.items()}
    return point, quantiles, shift


def _apply_replacements(inputs, forecasts, pairs):
    """Return (adjusted forecasts, [{line_id, known_months}] for lines the model continued)."""
    adjusted, continued = [], []
    for forecast in forecasts:
        indexes = {i for line_id, i in pairs if line_id == forecast.line_id}
        if not indexes:
            adjusted.append(forecast)
            continue
        lead = len(indexes)
        if indexes == set(range(lead)) and lead < HORIZON:      # the first months of the forecast
            point, quantiles, shift = _continue_from_known(inputs, forecast, lead)
            continued.append({"line_id": forecast.line_id, "known_months": lead,
                              "level_shift": f"{shift:.2f}"})
        else:
            point = tuple(0.0 if i in indexes else v for i, v in enumerate(forecast.point))
            quantiles = {q: tuple(0.0 if i in indexes else v for i, v in enumerate(values))
                         for q, values in forecast.quantiles.items()}
        adjusted.append(LineForecast(forecast.line_id, forecast.origin_month, forecast.model_name,
                                     point, quantiles))
    return tuple(adjusted), continued


def _scenario_assumptions(inputs, scenario, base_statement):
    rules = [r for r in inputs.rules if r.scenario == scenario]
    generated = []
    for month in base_statement.months:
        amounts = {line.line_id: line.point for line in month.lines}
        for rule in rules:
            if rule.from_month and month.month < rule.from_month:
                continue
            if rule.to_month and month.month > rule.to_month:
                continue
            if rule.percent is not None:
                before = amounts[rule.line_id]
                amount = (before * Decimal(str(rule.percent)) / 100).quantize(CENT, rounding=ROUND_HALF_EVEN)
                how = f"{rule.percent:+g}% of {before}"
            else:
                amount = Decimal(str(rule.amount)).quantize(CENT, rounding=ROUND_HALF_EVEN)
                how = "fixed amount"
            generated.append(ManagementAssumption(
                f"{scenario}_{rule.line_id}_{_month_text(month.month)}_row{rule.row}",
                rule.line_id, month.month, inputs.origin_month, amount, scenario,
                f"{rule.note} ({how})" if rule.note else how))
    return tuple(generated)


def run_all(inputs, model, scenarios=()):
    """Return {"base": run, "<scenario>": run, ...}. Models are invoked once."""
    engine_inputs = inputs.engine_kwargs()
    # Step 0: model forecasts only (no schedule, no scenario), plus the engine's model audit.
    models_only = run_cash_flow(**{**engine_inputs, "known_events": (), "assumptions": ()},
                                scenario_id=None, model_name=model, data_kind=inputs.data_kind)
    raw_forecasts = models_only.inputs["base_forecasts"]
    pairs = _replaced_months(inputs)
    forecasts, continued = _apply_replacements(inputs, raw_forecasts, pairs)
    replaced = [{"line_id": line_id, "month": _month_text(add_months(inputs.origin_month, i + 1))}
                for line_id, i in sorted(pairs)]

    def assemble(scenario, assumptions):
        statement = assemble_cash_flow(
            inputs.schema, inputs.origin_month, inputs.opening_cash, forecasts=forecasts,
            known_events=inputs.known_events, assumptions=assumptions, scenario_id=scenario)
        return replace(models_only, statement=statement, inputs={
            **models_only.inputs, "known_events": inputs.known_events, "assumptions": assumptions,
            "scenario_id": scenario, "base_forecasts": raw_forecasts,
            "model_forecast_replaced_by_known_amount": replaced,
            "model_continued_from_known_amounts": continued})

    runs = {BASE: assemble(None, tuple(inputs.assumptions))}
    for scenario in scenarios:
        assumptions = tuple(inputs.assumptions) + _scenario_assumptions(inputs, scenario, runs[BASE].statement)
        runs[scenario] = assemble(scenario, assumptions)
    return runs
