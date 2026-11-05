"""Deterministic 12-month direct cash-movement assembly and reconciliation."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Mapping

from .common import add_months
from .cash_flow_schema import (
    CashFlowSchema, HORIZON, KnownEvent, LineForecast, ManagementAssumption,
    OpeningCash, QUANTILES, SECTIONS, identifier, money, month, numeric,
)

ZERO = Decimal("0.00")


@dataclass(frozen=True)
class LineMovement:
    line_id: str
    base_point: Decimal
    known_overlay: Decimal
    assumption_overlay: Decimal
    point: Decimal
    # Conditional line amounts after deterministic additions, never joint cash quantiles.
    quantiles: Mapping[float, Decimal]
    model_name: str | None
    event_ids: tuple[str, ...]
    assumption_ids: tuple[str, ...]


@dataclass(frozen=True)
class InputDecision:
    kind: str
    input_id: str
    line_id: str
    reason: str


@dataclass(frozen=True)
class CashFlowMonth:
    month: date
    lines: tuple[LineMovement, ...]
    sections: Mapping[str, Decimal]
    net_cash_movement: Decimal
    opening_cash: Decimal
    closing_cash: Decimal


@dataclass(frozen=True)
class CashFlowStatement:
    schema_version: int
    currency: str
    origin_month: date
    scenario_id: str | None
    opening_cash: Decimal
    months: tuple[CashFlowMonth, ...]
    excluded_inputs: tuple[InputDecision, ...]


def _unique(items, field, label):
    ids = [getattr(item, field) for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate {label} ids")


def assemble_cash_flow(
    schema: CashFlowSchema,
    origin_month: date,
    opening_cash: OpeningCash,
    *,
    forecasts=(),
    known_events=(),
    assumptions=(),
    scenario_id=None,
):
    """Assemble already supplied forecasts; this engine never invokes models.

    Precedence is additive: base forecast, eligible known events, then explicitly
    selected scenario assumptions. Known/assumption treatment lines have zero
    base. Missing schedules mean zero; missing forecast lines are errors.
    """
    if not isinstance(schema, CashFlowSchema):
        raise ValueError("CashFlowSchema required")
    schema.__post_init__()
    month(origin_month, "origin_month")
    if not isinstance(opening_cash, OpeningCash):
        raise ValueError("OpeningCash input required")
    opening_cash.__post_init__()
    if opening_cash.as_of_month != origin_month:
        raise ValueError("opening cash must be provided as of origin_month")
    if scenario_id is not None:
        identifier(scenario_id, "scenario_id")
    lines = {line.line_id: line for line in schema.lines}
    forecasts, known_events, assumptions = map(tuple, (forecasts, known_events, assumptions))
    for items, contract in ((forecasts, LineForecast), (known_events, KnownEvent),
                            (assumptions, ManagementAssumption)):
        if any(not isinstance(item, contract) for item in items):
            raise ValueError(f"{contract.__name__} contracts required")
        for item in items:
            item.__post_init__()
    _unique(forecasts, "line_id", "forecast line")
    _unique(known_events, "event_id", "event")
    _unique(assumptions, "assumption_id", "assumption")
    required = {line.line_id for line in schema.lines if line.treatment == "forecast"}
    if {f.line_id for f in forecasts} != required:
        raise ValueError("provide exactly one forecast for every forecast treatment line")
    if any(f.origin_month != origin_month for f in forecasts):
        raise ValueError("forecast origin does not match statement origin")
    for event in known_events:
        if event.line_id not in lines:
            raise ValueError("known event references unknown line id")
        if lines[event.line_id].treatment == "assumption":
            raise ValueError("known events cannot fund an assumption treatment line")
    if any(a.line_id not in lines for a in assumptions):
        raise ValueError("assumption references unknown line id")
    if scenario_id is not None and not any(a.scenario_id == scenario_id for a in assumptions):
        raise ValueError("selected scenario_id has no supplied assumptions")

    forecast_map = {f.line_id: f for f in forecasts}
    targets = tuple(add_months(origin_month, i) for i in range(1, HORIZON + 1))
    events_by_key, assumptions_by_key, excluded = {}, {}, []
    for event in sorted(known_events, key=lambda e: e.event_id):
        if event.known_from_month > origin_month:
            reason = "not_known_at_origin"
        elif event.effective_month not in targets:
            reason = "outside_horizon"
        else:
            events_by_key.setdefault((event.line_id, event.effective_month), []).append(event)
            continue
        excluded.append(InputDecision("known", event.event_id, event.line_id, reason))
    for assumption in sorted(assumptions, key=lambda a: a.assumption_id):
        if scenario_id is None or assumption.scenario_id != scenario_id:
            reason = "scenario_not_selected"
        elif assumption.available_from_month > origin_month:
            reason = "not_available_at_origin"
        elif assumption.effective_month not in targets:
            reason = "outside_horizon"
        else:
            assumptions_by_key.setdefault((assumption.line_id, assumption.effective_month), []).append(assumption)
            continue
        excluded.append(InputDecision("assumption", assumption.assumption_id, assumption.line_id, reason))

    initial = money(opening_cash.amount)
    balance, result = initial, []
    for i, target in enumerate(targets):
        movements = []
        for line in schema.lines:
            forecast = forecast_map.get(line.line_id)
            base = money(forecast.point[i]) if forecast else ZERO
            events = events_by_key.get((line.line_id, target), [])
            adjustments = assumptions_by_key.get((line.line_id, target), [])
            known = sum((money(e.amount) for e in events), ZERO)
            assumed = sum((money(a.amount) for a in adjustments), ZERO)
            point = base + known + assumed
            quantiles = {q: money(values[i]) + known + assumed
                         for q, values in forecast.quantiles.items()} if forecast else {}
            movements.append(LineMovement(line.line_id, base, known, assumed, point,
                quantiles, forecast.model_name if forecast else None,
                tuple(e.event_id for e in events), tuple(a.assumption_id for a in adjustments)))
        totals = {section: sum((r.point for r in movements
                    if lines[r.line_id].section == section), ZERO) for section in SECTIONS}
        net = sum(totals.values(), ZERO)
        closing = balance + net
        result.append(CashFlowMonth(target, tuple(movements), totals, net, balance, closing))
        balance = closing
    statement = CashFlowStatement(schema.schema_version, schema.currency, origin_month,
                                  scenario_id, initial, tuple(result), tuple(excluded))
    validate_cash_flow(statement, schema)
    return statement


def _statement_money(value):
    numeric(value, "statement amount")
    if money(value) != value:
        raise ValueError("statement amounts must be rounded to cents")


def validate_cash_flow(statement: CashFlowStatement, schema: CashFlowSchema):
    """Independently recompute monthly sections and the full 12-month cash bridge.

    Decimal cents reconcile exactly: no tolerance can mask accounting differences.
    Does not certify provenance of external inputs; assemble_cash_flow enforces
    origin eligibility and treatment rules at the input boundary.
    """
    if not isinstance(statement, CashFlowStatement) or not isinstance(schema, CashFlowSchema):
        raise ValueError("statement and schema contracts required")
    schema.__post_init__()
    month(statement.origin_month, "origin_month")
    if statement.schema_version != schema.schema_version or statement.currency != schema.currency:
        raise ValueError("statement/schema metadata mismatch")
    if len(statement.months) != HORIZON:
        raise ValueError("statement must contain exactly 12 months")
    _statement_money(statement.opening_cash)
    expected_balance = statement.opening_cash
    line_map = {line.line_id: line for line in schema.lines}
    for i, row in enumerate(statement.months, 1):
        if row.month != add_months(statement.origin_month, i):
            raise ValueError("monthly roll-forward dates are inconsistent")
        ids = [line.line_id for line in row.lines]
        if len(ids) != len(set(ids)) or set(ids) != set(line_map):
            raise ValueError("statement must contain each schema line once per month")
        for line in row.lines:
            for value in (line.base_point, line.known_overlay, line.assumption_overlay, line.point):
                _statement_money(value)
            if line.base_point + line.known_overlay + line.assumption_overlay != line.point:
                raise ValueError("line components do not reconcile")
            treatment = line_map[line.line_id].treatment
            if treatment != "forecast" and (line.base_point != ZERO or line.model_name is not None):
                raise ValueError("nonforecast line has a forecast base")
            if treatment == "forecast":
                identifier(line.model_name, "forecast model_name")
            if treatment == "assumption" and line.known_overlay != ZERO:
                raise ValueError("assumption line has a known overlay")
            if line.quantiles:
                if treatment != "forecast" or set(line.quantiles) != set(QUANTILES):
                    raise ValueError("invalid statement line quantiles")
                for value in line.quantiles.values():
                    _statement_money(value)
                if not line.quantiles[0.1] <= line.quantiles[0.5] <= line.quantiles[0.9]:
                    raise ValueError("statement line quantiles cross")
                if line.point != line.quantiles[0.5]:
                    raise ValueError("statement point must equal q50")
        if set(row.sections) != set(SECTIONS):
            raise ValueError("statement is missing categories")
        expected_totals = {section: sum((line.point for line in row.lines
                            if line_map[line.line_id].section == section), ZERO)
                           for section in SECTIONS}
        for value in (*row.sections.values(), row.net_cash_movement, row.opening_cash, row.closing_cash):
            _statement_money(value)
        if dict(row.sections) != expected_totals:
            raise ValueError("section totals do not reconcile")
        if sum(row.sections.values(), ZERO) != row.net_cash_movement:
            raise ValueError("net cash movement does not reconcile")
        if row.opening_cash != expected_balance:
            raise ValueError("monthly opening cash roll-forward is inconsistent")
        if row.opening_cash + row.net_cash_movement != row.closing_cash:
            raise ValueError("opening/closing cash do not reconcile")
        expected_balance = row.closing_cash
    if statement.opening_cash + sum((r.net_cash_movement for r in statement.months), ZERO) != expected_balance:
        raise ValueError("12-month cash bridge does not reconcile")

