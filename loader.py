"""Read the cash-forecast template (one Excel workbook, or a folder of CSV files).

The template has four tables. The same names are used for Excel sheets and for
CSV files (inputs.csv, history.csv, ...):

  inputs        key / value: currency, origin_month, data_kind; optional model and
                minimum_cash (opening_cash only if the history has no closing_cash row)
  history       a direct cash-flow statement: an opening_cash row, one row per
                line, a closing_cash row. Columns: line_id, label, section,
                treatment, then one column per month
  known_events  line_id, month, amount, effect, known_from, note
  scenarios     scenario, line_id, percent, amount, from_month, to_month, note

A line is either "forecast" (a model projects it) or "known" (the schedule is
used). On a forecast line a known amount either replaces the model figure for
that month (effect = replace, the default) or is added to it (effect = on top).

A row whose first cell starts with "NOTE" above a table's headings is skipped,
so a sheet can carry a reminder for the user.

Older layouts are still read: a separate "lines" table (line_id, label, section,
treatment), tables called "settings" and "assumptions", an
event_id column, the treatment "assumption", and scenarios given as one amount
per month (month, amount, available_from, assumption_id).

Every problem is collected and reported in plain language, so the user can fix
the whole file in one pass. Nothing here calculates a forecast.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .engine.cash_flow_schema import (
    CashFlowLine, CashFlowSchema, HistoricalLineValue, KnownEvent,
    ManagementAssumption, OpeningCash, SECTIONS, TREATMENTS, HORIZON,
)
from .engine.common import add_months

TABLES = ("inputs", "history", "known_events", "scenarios")      # what the template contains
READABLE = ("inputs", "lines", "history", "known_events", "scenarios")
REQUIRED_TABLES = ("inputs", "history")
LINE_COLUMNS = ("label", "section", "treatment")
ALIASES = {"settings": "inputs", "assumptions": "scenarios"}
EFFECTS = {"": "replace", "replace": "replace", "replaces": "replace", "replace forecast": "replace",
           "on top": "on_top", "on_top": "on_top", "ontop": "on_top", "add": "on_top", "additional": "on_top"}
BASE = "base"
DATA_KINDS = ("synthetic", "actual", "unspecified")
BALANCE_ROWS = {"opening_cash": "opening_cash", "opening cash": "opening_cash",
                "opening balance": "opening_cash", "opening cash balance": "opening_cash",
                "closing_cash": "closing_cash", "closing cash": "closing_cash",
                "closing balance": "closing_cash", "closing cash balance": "closing_cash"}
MAX_FILE_BYTES = 20 * 1024 * 1024


class TemplateError(ValueError):
    """The input cannot be used. `problems` lists every issue found."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


@dataclass(frozen=True)
class ScenarioRule:
    """One row of the scenarios table: a percentage or a fixed amount on a line."""
    scenario: str
    line_id: str
    percent: float | None
    amount: float | None
    from_month: date | None
    to_month: date | None
    note: str
    row: int


@dataclass
class Inputs:
    schema: CashFlowSchema
    history: tuple
    origin_month: date
    opening_cash: OpeningCash
    known_events: tuple
    assumptions: tuple
    data_kind: str
    source: str
    warnings: list = field(default_factory=list)
    rules: tuple = ()                                # ScenarioRule rows (percent or amount)
    replace_ids: frozenset = frozenset()             # known events that replace the model figure
    model: str | None = None                         # optional choice made in the inputs sheet
    minimum_cash: float | None = None                # optional liquidity floor for the summary
    actuals: dict = field(default_factory=dict)      # line_id -> {month: amount}, every line supplied
    balances: dict = field(default_factory=dict)     # "opening_cash"/"closing_cash" -> {month: amount}
    opening_cash_source: str = "inputs"
    history_ties: bool | None = None                 # None = could not be checked

    @property
    def scenarios(self):
        return sorted({a.scenario_id for a in self.assumptions} | {r.scenario for r in self.rules})

    def history_months(self):
        """Usable months of history per forecast line (consecutive, ending at origin)."""
        counts = {}
        for row in self.history:
            if row.month <= self.origin_month:
                counts[row.line_id] = counts.get(row.line_id, 0) + 1
        return counts

    def engine_kwargs(self):
        return {"schema": self.schema, "history": self.history,
                "origin_month": self.origin_month, "opening_cash": self.opening_cash,
                "known_events": self.known_events, "assumptions": self.assumptions}


# --- reading raw tables -------------------------------------------------------
def _clean(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return value


def _rows_from_grid(grid):
    """First non-empty row is the header; later rows become dicts. Blank rows are skipped."""
    grid = [[_clean(c) for c in row] for row in grid]
    grid = [row for row in grid if any(c != "" for c in row)]
    while grid and isinstance(grid[0][0], str) and grid[0][0].upper().startswith("NOTE"):
        grid = grid[1:]         # a reminder for the user, not data
    if not grid:
        return [], []
    header = [_header_text(c) for c in grid[0]]
    while header and header[-1] == "":
        header.pop()
    rows = []
    for raw in grid[1:]:
        cells = list(raw[:len(header)]) + [""] * (len(header) - len(raw))
        rows.append(dict(zip(header, cells)))
    return header, rows


def _header_text(cell):
    if isinstance(cell, (datetime, date)):
        return f"{cell.year:04d}-{cell.month:02d}"
    return str(cell).strip()


def _read_csv_folder(folder):
    tables = {}
    for file_name in (*READABLE, *ALIASES):
        name = ALIASES.get(file_name, file_name)
        path = folder / f"{file_name}.csv"
        if not path.is_file() or name in tables:
            continue
        _check_size(path)
        with path.open(newline="", encoding="utf-8-sig") as file:
            sample = file.read(4096)
            file.seek(0)
            delimiter = ";" if sample.count(";") > sample.count(",") else ","
            tables[name] = _rows_from_grid(list(csv.reader(file, delimiter=delimiter)))
    return tables


def _read_workbook(path):
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise TemplateError(["Reading Excel files needs the 'openpyxl' package. "
                             "Install it, or save the five sheets as CSV files in one folder."]) from exc
    _check_size(path)
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        by_name = {}
        for sheet in book.worksheets:
            title = sheet.title.strip().lower()
            by_name.setdefault(ALIASES.get(title, title), sheet)
        return {name: _rows_from_grid(list(by_name[name].iter_rows(values_only=True)))
                for name in READABLE if name in by_name}
    finally:
        book.close()


def _check_size(path):
    if path.stat().st_size > MAX_FILE_BYTES:
        raise TemplateError([f"{path.name} is larger than 20 MB; this template is meant for monthly data."])


def read_tables(input_path):
    path = Path(str(input_path)).expanduser()
    if path.is_dir():
        return _read_csv_folder(path), str(path)
    if path.is_file() and path.suffix.lower() in (".xlsx", ".xlsm"):
        return _read_workbook(path), str(path)
    if path.is_file():
        raise TemplateError([f"'{path.name}' is not supported. Give an .xlsx workbook, "
                             "or a folder that contains the CSV files."])
    raise TemplateError([f"Nothing found at '{path}'. Give an .xlsx workbook or a folder of CSV files."])


# --- converting cells ---------------------------------------------------------
def parse_month(value):
    """'2026-09', '2026-09-01', '09/2026', 'Sep-26' or an Excel date -> date(2026, 9, 1)."""
    if isinstance(value, datetime):
        return date(value.year, value.month, 1)
    if isinstance(value, date):
        return date(value.year, value.month, 1)
    text = str(value).strip()
    for pattern in ("%Y-%m", "%Y-%m-%d", "%m/%Y", "%Y/%m", "%b-%y", "%b %Y", "%b-%Y", "%d.%m.%Y", "%m.%Y"):
        try:
            parsed = datetime.strptime(text, pattern)
            return date(parsed.year, parsed.month, 1)
        except ValueError:
            continue
    raise ValueError(f"'{text}' is not a month (use YYYY-MM, for example 2026-09)")


def parse_amount(value):
    """Numbers, or text such as '1,234.50', '1.234,50', '(500)' or '-500'."""
    if isinstance(value, bool):
        raise ValueError("not a number")
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip().replace(" ", "").replace(" ", "")
        negative = text.startswith("(") and text.endswith(")")
        if negative:
            text = text[1:-1]
        if "," in text and "." in text:
            if text.rfind(",") > text.rfind("."):      # 1.234,50
                text = text.replace(".", "").replace(",", ".")
            else:                                        # 1,234.50
                text = text.replace(",", "")
        elif "," in text:
            head, _, tail = text.rpartition(",")
            text = head.replace(",", "") + ("." + tail if len(tail) != 3 else tail)
        try:
            number = float(text)
        except ValueError as exc:
            raise ValueError(f"'{value}' is not a number") from exc
        if negative:
            number = -number
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError("not a finite number")
    return number


def parse_percent(value):
    """-5, '-5', '-5%' or '-5,5 %' -> -5.0 (percentage points)."""
    if isinstance(value, str):
        value = value.strip().rstrip("%").strip()
    number = parse_amount(value)
    if abs(number) > 100:
        raise ValueError(f"percent {number:g} is outside -100 to 100")
    return number


def _fmt(month):
    return f"{month.year:04d}-{month.month:02d}"


# --- building engine inputs ---------------------------------------------------
def load_inputs(input_path):
    """Return validated Inputs, or raise TemplateError listing every problem."""
    tables, source = read_tables(input_path)
    problems, warnings = [], []
    for name in REQUIRED_TABLES:
        if name not in tables:
            problems.append(f"The '{name}' table is missing.")
    if problems:
        raise TemplateError(problems)

    # inputs
    settings = {}
    header, rows = tables["inputs"]
    if [h.lower() for h in header[:2]] != ["key", "value"]:
        problems.append("inputs: the first two columns must be 'key' and 'value'.")
    else:
        settings = {str(r["key"]).strip().lower(): r["value"] for r in rows if str(r["key"]).strip()}
    currency = str(settings.get("currency", "")).strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        problems.append("inputs: 'currency' must be a three-letter code such as EUR.")
    origin = None
    try:
        origin = parse_month(settings.get("origin_month", ""))
    except ValueError as exc:
        problems.append(f"inputs: origin_month (the last month of actuals): {exc}.")
    opening = None
    if settings.get("opening_cash", "") != "":
        try:
            opening = parse_amount(settings["opening_cash"])
        except ValueError:
            problems.append("inputs: 'opening_cash' must be a number: the cash balance at the end of origin_month.")
    chosen_model = str(settings.get("model", "") or "").strip().lower() or None
    minimum_cash = None
    if settings.get("minimum_cash", "") != "":
        try:
            minimum_cash = parse_amount(settings["minimum_cash"])
        except ValueError:
            problems.append("inputs: 'minimum_cash' must be a number (or left empty).")
    data_kind = str(settings.get("data_kind", "") or "unspecified").strip().lower()
    if data_kind not in DATA_KINDS:
        problems.append("inputs: 'data_kind' must be synthetic, actual or unspecified.")

    # lines: defined in the history sheet (label, section, treatment next to each line),
    # or in a separate "lines" table in older files
    lines, seen = [], set()
    history_header, history_rows = tables["history"]
    if "lines" in tables:
        table = "lines"
        header, rows = tables["lines"]
        definitions = list(enumerate(rows, start=2))
    else:
        table = "history"
        header = history_header
        definitions = [(number, row) for number, row in enumerate(history_rows, start=2)
                       if str(row.get("line_id", "")).strip()
                       and str(row.get("line_id", "")).strip().lower() not in BALANCE_ROWS]
    missing = [c for c in ("line_id", "label", "section", "treatment") if c not in header]
    if missing:
        problems.append(f"{table}: missing column(s): {', '.join(missing)}. Each line needs a label, "
                        "a section and a treatment.")
    else:
        for number, row in definitions:
            line_id = str(row["line_id"]).strip()
            label = str(row["label"]).strip() or line_id
            section = str(row["section"]).strip().lower()
            treatment = str(row["treatment"]).strip().lower()
            where = f"{table} row {number} ('{line_id}')"
            if not line_id:
                problems.append(f"{table} row {number}: line_id is empty.")
                continue
            if line_id in seen:
                problems.append(f"{where}: line_id is used twice.")
                continue
            if section not in SECTIONS:
                problems.append(f"{where}: section must be operating, investing or financing.")
                continue
            if treatment not in TREATMENTS:      # "assumption" is still read from older files
                problems.append(f"{where}: treatment must be forecast or known.")
                continue
            seen.add(line_id)
            lines.append(CashFlowLine(line_id, label, section, treatment))
    by_id = {line.line_id: line for line in lines}
    if lines:
        absent = [s for s in SECTIONS if s not in {line.section for line in lines}]
        if absent:
            problems.append("The cash-flow statement needs at least one line in each section; "
                            f"none found for: {', '.join(absent)}. Add a line (it may stay at zero).")
        if not any(line.treatment == "forecast" for line in lines):
            warnings.append("No line has treatment 'forecast', so no model will run; "
                            "the result is only the known schedule and the scenarios.")

    # history (a direct cash-flow statement: opening cash, one row per line, closing cash)
    history, actuals, balances = [], {}, {"opening_cash": {}, "closing_cash": {}}
    header, rows = history_header, history_rows
    if not header or header[0] != "line_id":
        problems.append("history: the first column must be 'line_id'.")
    else:
        months = []
        for column in header[1:]:
            if column.lower() in LINE_COLUMNS:
                continue
            try:
                months.append((column, parse_month(column)))
            except ValueError as exc:
                problems.append(f"history: column heading {exc}.")
        if len({m for _, m in months}) != len(months):
            problems.append("history: the same month appears in two columns.")
        filled = set()
        for number, row in enumerate(rows, start=2):
            line_id = str(row["line_id"]).strip()
            if not line_id:
                continue
            balance = BALANCE_ROWS.get(line_id.lower())
            if balance:
                for column, month in months:
                    cell = row.get(column, "")
                    if cell == "":
                        continue
                    try:
                        balances[balance][month] = parse_amount(cell)
                    except ValueError:
                        problems.append(f"history '{line_id}', {_fmt(month)}: '{cell}' is not a number.")
                continue
            if line_id not in by_id:
                if "lines" in tables:
                    problems.append(f"history row {number}: '{line_id}' is not in the lines table.")
                continue
            if line_id in filled:
                problems.append(f"history row {number}: '{line_id}' appears twice.")
                continue
            filled.add(line_id)
            is_forecast = by_id[line_id].treatment == "forecast"
            values = {}
            for column, month in months:
                cell = row.get(column, "")
                if cell == "":
                    continue
                try:
                    values[month] = parse_amount(cell)
                except ValueError:
                    problems.append(f"history '{line_id}', {_fmt(month)}: '{cell}' is not a number.")
            actuals[line_id] = values
            if not is_forecast:
                continue    # used for the statement and the tie-out check, not by the model
            if origin is None or not values:
                if not values:
                    problems.append(f"history '{line_id}': no values. A forecast line needs monthly history.")
                continue
            later = sorted(m for m in values if m > origin)
            if later:
                warnings.append(f"history '{line_id}': {len(later)} month(s) after origin_month "
                                f"{_fmt(origin)} are ignored.")
            if origin not in values:
                problems.append(f"history '{line_id}': no value for origin_month {_fmt(origin)}. "
                                "History must run up to and include the last month of actuals.")
                continue
            # walk back from the origin; stop at the first gap
            run, month = [], origin
            while month in values:
                run.append(month)
                month = add_months(month, -1)
            older = [m for m in values if m < run[-1]]
            if older:
                problems.append(f"history '{line_id}': {_fmt(month)} is empty but earlier months have values. "
                                "Fill the gap (use 0 if there was truly no cash flow) or delete the older months.")
                continue
            history.extend(HistoricalLineValue(line_id, m, values[m]) for m in reversed(run))
        for line in lines:
            if line.treatment == "forecast" and line.line_id not in filled:
                problems.append(f"history: forecast line '{line.line_id}' has no row.")

    # known events
    events, replace_ids = [], set()
    if "known_events" in tables and tables["known_events"][0]:
        header, rows = tables["known_events"]
        missing = [c for c in ("line_id", "month", "amount") if c not in header]
        if missing:
            problems.append(f"known_events: missing column(s): {', '.join(missing)}.")
        else:
            ids = set()
            for number, row in enumerate(rows, start=2):
                where = f"known_events row {number}"
                line_id = str(row["line_id"]).strip()
                if line_id not in by_id:
                    problems.append(f"{where}: '{line_id}' is not one of the lines in the history sheet.")
                    continue
                if by_id[line_id].treatment == "assumption":
                    problems.append(f"{where}: '{line_id}' has treatment 'assumption'; put this row in the scenarios table.")
                    continue
                try:
                    effective = parse_month(row["month"])
                    amount = parse_amount(row["amount"])
                    known_from = row.get("known_from", "")
                    known_from = parse_month(known_from) if known_from != "" else (
                        min(origin, effective) if origin else effective)
                except ValueError as exc:
                    problems.append(f"{where}: {exc}.")
                    continue
                if effective < known_from:
                    problems.append(f"{where}: known_from is after the month the cash moves.")
                    continue
                effect = EFFECTS.get(str(row.get("effect", "")).strip().lower())
                if effect is None:
                    problems.append(f"{where}: effect must be 'replace' or 'on top' (empty means replace).")
                    continue
                event_id = str(row.get("event_id", "")).strip() or f"{line_id}_{_fmt(effective)}_row{number}"
                if event_id in ids:
                    problems.append(f"{where}: event_id '{event_id}' is used twice.")
                    continue
                ids.add(event_id)
                events.append(KnownEvent(event_id, line_id, effective, known_from, amount,
                                         str(row.get("note", "")).strip()))
                if effect == "replace" and by_id[line_id].treatment == "forecast":
                    replace_ids.add(event_id)

    # scenarios: a percentage or a fixed amount per line, over a range of months
    assumptions, rules = [], []
    if "scenarios" in tables and tables["scenarios"][0]:
        header, rows = tables["scenarios"]
        legacy = "month" in header and "percent" not in header     # one amount per month
        needed = ("scenario", "line_id", "month", "amount") if legacy else ("scenario", "line_id")
        missing = [c for c in needed if c not in header]
        if not legacy and "percent" not in header and "amount" not in header:
            missing.append("percent (or amount)")
        if missing:
            problems.append(f"scenarios: missing column(s): {', '.join(missing)}.")
        else:
            ids = set()
            for number, row in enumerate(rows, start=2):
                where = f"scenarios row {number}"
                line_id = str(row["line_id"]).strip()
                scenario = str(row["scenario"]).strip()
                if line_id not in by_id:
                    problems.append(f"{where}: '{line_id}' is not one of the lines in the history sheet.")
                    continue
                if not scenario:
                    problems.append(f"{where}: scenario is empty.")
                    continue
                if scenario.lower() in (BASE, "base case"):
                    problems.append(f"{where}: 'base' is the forecast without any scenario; choose another name.")
                    continue
                note = str(row.get("note", "")).strip()
                if legacy:
                    try:
                        effective = parse_month(row["month"])
                        amount = parse_amount(row["amount"])
                        available = row.get("available_from", "")
                        available = parse_month(available) if available != "" else (
                            min(origin, effective) if origin else effective)
                    except ValueError as exc:
                        problems.append(f"{where}: {exc}.")
                        continue
                    if effective < available:
                        problems.append(f"{where}: available_from is after the month the cash moves.")
                        continue
                    assumption_id = (str(row.get("assumption_id", "")).strip()
                                     or f"{scenario}_{line_id}_{_fmt(effective)}_row{number}")
                    if assumption_id in ids:
                        problems.append(f"{where}: assumption_id '{assumption_id}' is used twice.")
                        continue
                    ids.add(assumption_id)
                    assumptions.append(ManagementAssumption(
                        assumption_id, line_id, effective, available, amount, scenario,
                        note or "No note given"))
                    continue
                percent_cell, amount_cell = row.get("percent", ""), row.get("amount", "")
                if (percent_cell == "") == (amount_cell == ""):
                    problems.append(f"{where}: fill either 'percent' or 'amount', not both and not neither.")
                    continue
                try:
                    percent = parse_percent(percent_cell) if percent_cell != "" else None
                    amount = parse_amount(amount_cell) if amount_cell != "" else None
                    start = parse_month(row["from_month"]) if row.get("from_month", "") != "" else None
                    end = parse_month(row["to_month"]) if row.get("to_month", "") != "" else None
                except ValueError as exc:
                    problems.append(f"{where}: {exc}.")
                    continue
                if percent is not None and by_id[line_id].treatment == "assumption":
                    problems.append(f"{where}: '{line_id}' has no forecast or schedule, so there is nothing to "
                                    "take a percentage of. Give an amount instead.")
                    continue
                if start and end and end < start:
                    problems.append(f"{where}: to_month is before from_month.")
                    continue
                if percent is not None and isinstance(percent_cell, (int, float)) and 0 < abs(percent) < 1:
                    warnings.append(f"{where}: percent {percent} is read as {percent}%. If the cell is formatted "
                                    f"as a percentage and you meant {percent * 100:g}%, type {percent * 100:g}.")
                rules.append(ScenarioRule(scenario, line_id, percent, amount, start, end, note, number))

    # opening cash for the forecast = closing cash at the end of origin_month
    opening_source = "inputs"
    if origin is not None:
        from_history = balances["closing_cash"].get(origin)
        if from_history is not None:
            if opening is not None and abs(opening - from_history) > 0.005:
                problems.append(f"Opening cash is given twice and differs: inputs says {opening:,.2f}, the "
                                f"closing_cash row for {_fmt(origin)} says {from_history:,.2f}. Remove one.")
            opening, opening_source = from_history, "history closing_cash row"
        elif opening is None:
            problems.append(f"No opening cash for the forecast. Add a 'closing_cash' row to the history with a "
                            f"value for {_fmt(origin)}, or give 'opening_cash' in inputs.")

    if problems:
        raise TemplateError(problems)

    # does the history tie? opening + all lines = closing, and closing = next opening
    ties = None
    opening_row, closing_row = balances["opening_cash"], balances["closing_cash"]
    if opening_row and closing_row:
        without_history = [l.line_id for l in lines if l.treatment != "assumption" and not actuals.get(l.line_id)]
        if without_history:
            warnings.append("The history could not be tied from opening to closing cash because these lines "
                            "have no history: " + ", ".join(without_history) + ".")
        else:
            breaks = []
            for month in sorted(set(opening_row) & set(closing_row)):
                movement = sum(values.get(month, 0.0) for values in actuals.values())
                gap = closing_row[month] - opening_row[month] - movement
                if abs(gap) > 0.005:
                    breaks.append(f"{_fmt(month)} ({gap:+,.2f})")
                following = add_months(month, 1)
                if following in opening_row and abs(opening_row[following] - closing_row[month]) > 0.005:
                    breaks.append(f"{_fmt(following)} opening differs from {_fmt(month)} closing")
            ties = not breaks
            if breaks:
                warnings.append(f"The history does not tie in {len(breaks)} place(s): opening cash + all lines "
                                "should equal closing cash. First differences: " + "; ".join(breaks[:6]) +
                                ". A line may be missing (for example FX effect on cash) or an amount is wrong.")
    elif not closing_row:
        warnings.append("The history has no opening_cash and closing_cash rows, so it could not be checked "
                        "from opening to closing cash.")

    # treasury sense checks (warnings only; the forecast can still run)
    horizon = {add_months(origin, i) for i in range(1, HORIZON + 1)}
    in_horizon = [e for e in events if e.effective_month in horizon and e.known_from_month <= origin]
    on_top = sorted({e.line_id for e in in_horizon
                     if by_id[e.line_id].treatment == "forecast" and e.event_id not in replace_ids})
    if on_top:
        warnings.append("Known amounts are added ON TOP of the model forecast on line(s) " + ", ".join(on_top) +
                        ". Make sure the history of these lines does not already contain the same kind "
                        "of item, or it is counted twice.")
    touched = {r.line_id for r in rules} | {a.line_id for a in assumptions}
    for line in lines:
        if (line.treatment == "known" and line.line_id not in touched
                and not any(e.line_id == line.line_id for e in in_horizon)):
            warnings.append(f"Known line '{line.line_id}' has no scheduled amount in the next 12 months; "
                            "it will be zero.")
    skipped = [e for e in events if e not in in_horizon]
    if skipped:
        warnings.append(f"{len(skipped)} known amount(s) fall outside the next 12 months or were not yet "
                        "known at origin_month; they are left out and listed in the result.")

    return Inputs(CashFlowSchema(currency, tuple(lines)), tuple(history), origin,
                  OpeningCash(origin, opening), tuple(events), tuple(assumptions),
                  data_kind, source, warnings, tuple(rules), frozenset(replace_ids), chosen_model,
                  minimum_cash, actuals, balances, opening_source, ties)
