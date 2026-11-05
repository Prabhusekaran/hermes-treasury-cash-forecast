"""Turn scenario rows (a percentage or a fixed amount on a line) into engine assumptions.

The engine applies assumptions as amounts. A percentage therefore has to be
converted, and it is converted from the engine's own base figures: the line's
amount in that month before any assumption (model forecast plus known schedule).
The models run once; the scenario is then assembled on top of the same base.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, ROUND_HALF_EVEN

from .engine.cash_flow import assemble_cash_flow
from .engine.cash_flow_orchestration import run_cash_flow
from .engine.cash_flow_schema import ManagementAssumption

CENT = Decimal("0.01")


def _month_text(month):
    return f"{month.year:04d}-{month.month:02d}"


def run_with_scenario(inputs, model, scenario):
    """Run the forecast. `scenario` is a name from the assumptions table, or ''."""
    kwargs = inputs.engine_kwargs()
    rules = [r for r in inputs.rules if r.scenario == scenario] if scenario else []
    if not rules:
        return run_cash_flow(**kwargs, scenario_id=scenario or None, model_name=model,
                             data_kind=inputs.data_kind)

    base = run_cash_flow(**kwargs, scenario_id=None, model_name=model, data_kind=inputs.data_kind)
    generated = []
    for month in base.statement.months:
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
            note = f"{rule.note} ({how})" if rule.note else how
            generated.append(ManagementAssumption(
                f"{scenario}_{rule.line_id}_{_month_text(month.month)}_row{rule.row}",
                rule.line_id, month.month, inputs.origin_month, amount, scenario, note))
    assumptions = tuple(inputs.assumptions) + tuple(generated)
    statement = assemble_cash_flow(
        inputs.schema, inputs.origin_month, inputs.opening_cash,
        forecasts=base.inputs["base_forecasts"], known_events=inputs.known_events,
        assumptions=assumptions, scenario_id=scenario)
    return replace(base, statement=statement,
                   inputs={**base.inputs, "assumptions": assumptions, "scenario_id": scenario})
