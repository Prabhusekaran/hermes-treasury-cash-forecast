"""Canonical, company-agnostic contracts for the 12-month direct cash engine.

Amounts are signed cash movements (receipts positive, payments negative).
The CFS schema version is independent of the existing evaluator schema.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from numbers import Real
from typing import Iterable, Mapping, Protocol, Sequence
import math

from .common import add_months

SCHEMA_VERSION = 1
HORIZON = 12
SECTIONS = ("operating", "investing", "financing")
TREATMENTS = ("forecast", "known", "assumption")
QUANTILES = (0.1, 0.5, 0.9)
CENT = Decimal("0.01")


def identifier(value, name):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be a nonempty trimmed string")


def month(value, name):
    if type(value) is not date or value.day != 1:
        raise ValueError(f"{name} must be a first-of-month date")


def numeric(value, name):
    if isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
        raise ValueError(f"{name} must be finite numeric")
    try:
        finite = value.is_finite() if isinstance(value, Decimal) else math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        finite = False
    if not finite:
        raise ValueError(f"{name} must be finite numeric")
    return value


def money(value):
    """Convert finite numbers to statement cents; half-even at component boundary."""
    numeric(value, "amount")
    try:
        return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_EVEN)
    except InvalidOperation as exc:
        raise ValueError("amount exceeds supported decimal precision") from exc


@dataclass(frozen=True)
class CashFlowLine:
    line_id: str
    label: str
    section: str
    treatment: str

    def __post_init__(self):
        identifier(self.line_id, "line_id")
        identifier(self.label, "label")
        if self.section not in SECTIONS:
            raise ValueError("line section/category must be operating, investing or financing")
        if self.treatment not in TREATMENTS:
            raise ValueError("line treatment must be forecast, known or assumption")


@dataclass(frozen=True)
class CashFlowSchema:
    currency: str
    lines: tuple[CashFlowLine, ...]
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported cash-flow schema_version")
        if (not isinstance(self.currency, str) or len(self.currency) != 3
                or not self.currency.isascii() or not self.currency.isalpha()
                or not self.currency.isupper()):
            raise ValueError("currency must be one uppercase three-letter currency code")
        object.__setattr__(self, "lines", tuple(self.lines))
        if not self.lines or any(not isinstance(line, CashFlowLine) for line in self.lines):
            raise ValueError("schema lines must be CashFlowLine contracts")
        ids = [line.line_id for line in self.lines]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate line ids")
        if {line.section for line in self.lines} != set(SECTIONS):
            raise ValueError("missing categories: schema must represent all three sections")

    @classmethod
    def from_dict(cls, data):
        """Small strict JSON boundary for the canonical schema, not a company mapper."""
        if not isinstance(data, dict) or set(data) != {"schema_version", "currency", "lines"}:
            raise ValueError("schema requires schema_version, currency and lines only")
        if not isinstance(data["lines"], list):
            raise ValueError("schema lines must be a list")
        lines = []
        for row in data["lines"]:
            if not isinstance(row, dict) or set(row) != {"line_id", "label", "section", "treatment"}:
                raise ValueError("line requires line_id, label, section/category and treatment only")
            lines.append(CashFlowLine(**row))
        return cls(data["currency"], tuple(lines), data["schema_version"])


@dataclass(frozen=True)
class HistoricalLineValue:
    line_id: str
    month: date
    amount: Real | Decimal

    def __post_init__(self):
        identifier(self.line_id, "line_id")
        month(self.month, "history month")
        numeric(self.amount, "history amount")


@dataclass(frozen=True)
class ForecastRequest:
    line_id: str
    origin_month: date
    model_name: str
    history: tuple[HistoricalLineValue, ...]
    horizon: int = HORIZON

    def __post_init__(self):
        identifier(self.line_id, "line_id")
        identifier(self.model_name, "model_name")
        month(self.origin_month, "origin_month")
        if type(self.horizon) is not int or self.horizon != HORIZON:
            raise ValueError("cash-flow forecast horizon must be exactly 12")
        object.__setattr__(self, "history", tuple(self.history))
        if not self.history or any(not isinstance(r, HistoricalLineValue) for r in self.history):
            raise ValueError("forecast history must contain HistoricalLineValue rows")
        if any(r.line_id != self.line_id for r in self.history):
            raise ValueError("history contains another line id")
        if any(r.month > self.origin_month for r in self.history):
            raise ValueError("future history beyond origin is forbidden")
        if self.history[-1].month != self.origin_month:
            raise ValueError("history must end at origin_month")
        if any(b.month != add_months(a.month, 1) for a, b in zip(self.history, self.history[1:])):
            raise ValueError("history months must be unique, ordered and consecutive")


@dataclass(frozen=True)
class LineForecast:
    line_id: str
    origin_month: date
    model_name: str
    point: tuple[Real, ...]
    quantiles: Mapping[float, Sequence[Real]] = field(default_factory=dict)

    def __post_init__(self):
        identifier(self.line_id, "line_id")
        identifier(self.model_name, "model_name")
        month(self.origin_month, "origin_month")
        object.__setattr__(self, "point", tuple(self.point))
        if len(self.point) != HORIZON:
            raise ValueError("point forecast must contain 12 months")
        for value in self.point:
            numeric(value, "forecast point")
        if not isinstance(self.quantiles, Mapping):
            raise ValueError("forecast quantiles must be a mapping")
        if self.quantiles and set(self.quantiles) != set(QUANTILES):
            raise ValueError("optional quantiles must contain exactly q10/q50/q90")
        clean = {q: tuple(values) for q, values in self.quantiles.items()}
        for values in clean.values():
            if len(values) != HORIZON:
                raise ValueError("quantiles must contain 12 months")
            for value in values:
                numeric(value, "forecast quantile")
        if clean:
            for i in range(HORIZON):
                if not clean[0.1][i] <= clean[0.5][i] <= clean[0.9][i]:
                    raise ValueError("forecast quantiles cross")
                if self.point[i] != clean[0.5][i]:
                    raise ValueError("quantile-capable forecast point must equal q50")
        # Immutable sequences; validation is repeated at engine boundary.
        object.__setattr__(self, "quantiles", clean)


@dataclass(frozen=True)
class KnownEvent:
    event_id: str
    line_id: str
    effective_month: date
    known_from_month: date
    amount: Real | Decimal
    note: str = ""

    def __post_init__(self):
        identifier(self.event_id, "event_id")
        identifier(self.line_id, "line_id")
        month(self.effective_month, "effective_month")
        month(self.known_from_month, "known_from_month")
        numeric(self.amount, "event amount")
        if self.effective_month < self.known_from_month:
            raise ValueError("event effective_month is before known_from_month")


@dataclass(frozen=True)
class ManagementAssumption:
    assumption_id: str
    line_id: str
    effective_month: date
    available_from_month: date
    amount: Real | Decimal
    scenario_id: str
    note: str

    def __post_init__(self):
        for name in ("assumption_id", "line_id", "scenario_id", "note"):
            identifier(getattr(self, name), name)
        month(self.effective_month, "effective_month")
        month(self.available_from_month, "available_from_month")
        numeric(self.amount, "assumption amount")
        if self.effective_month < self.available_from_month:
            raise ValueError("assumption effective_month is before available_from_month")


@dataclass(frozen=True)
class OpeningCash:
    as_of_month: date
    amount: Real | Decimal

    def __post_init__(self):
        month(self.as_of_month, "opening cash as_of_month")
        money(self.amount)


class CashFlowMapping(Protocol):
    """Private implementations map source data to these generic signed contracts.

    The core neither reads private source formats nor defines company mappings.
    Historical amounts for forecast lines must already represent recurring flow.
    """
    def schema(self) -> CashFlowSchema: ...
    def history(self, rows: Iterable[Mapping[str, object]]) -> Sequence[HistoricalLineValue]: ...
    def known_events(self, rows: Iterable[Mapping[str, object]]) -> Sequence[KnownEvent]: ...

