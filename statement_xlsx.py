"""Write the forecast as one Excel workbook: a summary, then one cash-flow statement per scenario.

Each statement runs from opening cash to closing cash. Columns: the last actual
months (from the history the user supplied), then the 12 forecast months. Every
figure is the engine's; the workbook holds values, not formulas, so nothing can
drift from the reconciled result.
"""
from __future__ import annotations

from .engine.cash_flow_schema import SECTIONS

ACTUAL_MONTHS_SHOWN = 12
BASE = "base"
SECTION_TITLES = {"operating": "Operating activities", "investing": "Investing activities",
                  "financing": "Financing activities"}
NUMBER = '#,##0;[Red]-#,##0'
FORBIDDEN = set('[]:*?/\\')


def _label(month):
    return f"{month.year:04d}-{month.month:02d}"


def _title(name):
    return "Base case" if name == BASE else name


def _ranges(indexes, months):
    """[0, 1, 5] -> '2026-10 to 2026-11, 2027-03'."""
    groups, indexes = [], sorted(indexes)
    for i in indexes:
        if groups and i == groups[-1][1] + 1:
            groups[-1][1] = i
        else:
            groups.append([i, i])
    return ", ".join(_label(months[a]) if a == b else f"{_label(months[a])} to {_label(months[b])}"
                     for a, b in groups)


def describe_treatments(run, replaced):
    """Per line: the words for the Treatment column and the source of each forecast month.

    Sources: 'model', 'known' (a known line, or a known amount that replaced the model figure)
    and 'on_top' (model figure plus a known amount).
    """
    months = [m.month for m in run.statement.months]
    result = {}
    for position, line in enumerate(run.schema.lines):
        if line.treatment != "forecast":
            text = "known" if line.treatment == "known" else "scenario only"
            result[line.line_id] = (text, ["known"] * len(months))
            continue
        swapped = [i for i, m in enumerate(months) if (line.line_id, _label(m)) in replaced]
        on_top = [i for i, m in enumerate(run.statement.months)
                  if i not in swapped and m.lines[position].known_overlay]
        if not swapped:
            text = "forecast"
        elif swapped == list(range(len(swapped))) and len(swapped) < len(months):
            text = f"known {_ranges(swapped, months)}, then forecast"
        elif len(swapped) == len(months):
            text = "known in every month"
        else:
            text = f"forecast; known in {_ranges(swapped, months)}"
        if on_top:
            text += f", plus known on top {_ranges(on_top, months)}"
        sources = ["known" if i in swapped else "on_top" if i in on_top else "model" for i in range(len(months))]
        result[line.line_id] = (text, sources)
    return result


def _sheet_names(names):
    used, result = {"summary", "where numbers come from", "notes"}, {}
    for name in names:
        clean = "".join("_" if c in FORBIDDEN else c for c in _title(name)).strip("'")[:28] or "scenario"
        candidate, count = clean, 2
        while candidate.lower() in used:
            candidate, count = f"{clean[:25]} ({count})", count + 1
        used.add(candidate.lower())
        result[name] = candidate
    return result


def write_workbook(path, runs, inputs):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    bold, white, italic = Font(bold=True), Font(bold=True, color="FFFFFF"), Font(italic=True)
    head_fill = PatternFill("solid", fgColor="1F3864")
    actual_fill = PatternFill("solid", fgColor="EDEDED")
    total_fill = PatternFill("solid", fgColor="DDEBF7")
    cash_fill = PatternFill("solid", fgColor="FFF2CC")
    known_fill = PatternFill("solid", fgColor="E2EFDA")
    on_top_fill = PatternFill("solid", fgColor="FCE4D6")
    source_fill = {"known": known_fill, "on_top": on_top_fill, "model": None}
    right = Alignment(horizontal="right")

    base = runs[BASE]
    schema = base.schema
    definitions = {line.line_id: line for line in schema.lines}
    currency = base.statement.currency
    models = ", ".join(sorted({a["used_model"] for a in base.model_audit})) or "none"
    forecast_months = [m.month for m in base.statement.months]
    supplied = sorted({month for values in inputs.actuals.values() for month in values
                       if month <= inputs.origin_month})
    actual_months = supplied[-ACTUAL_MONTHS_SHOWN:]
    months = actual_months + forecast_months
    complete = all(inputs.actuals.get(line.line_id) for line in schema.lines if line.treatment != "assumption")
    replaced = {(item["line_id"], item["month"])
                for item in base.inputs.get("model_forecast_replaced_by_known_amount", [])}
    names = _sheet_names(runs)
    treatments = describe_treatments(base, replaced)

    book = Workbook()

    def number_cell(sheet, row, column, value, *, font=None, fill=None):
        cell = sheet.cell(row, column, None if value is None else float(value))
        cell.number_format = NUMBER
        if font:
            cell.font = font
        if fill:
            cell.fill = fill
        return cell

    # --- Summary ------------------------------------------------------------------
    summary = book.active
    summary.title = "Summary"
    summary["A1"] = f"12-month cash forecast ({currency})"
    summary["A1"].font = Font(bold=True, size=14)
    summary["A2"] = (f"Forecast from {_label(inputs.origin_month)} (last month of actuals). Model: {models}. "
                     f"Data: {base.data_kind}. Opening cash {float(base.statement.opening_cash):,.2f}.")
    row = 4
    summary.cell(row, 1, "Closing cash by month").font = white
    for column, name in enumerate(runs, start=2):
        cell = summary.cell(row, column, _title(name))
        cell.font, cell.alignment = white, right
    for column in range(1, 2 + len(runs)):
        summary.cell(row, column).fill = head_fill
    for index, month in enumerate(forecast_months):
        row += 1
        summary.cell(row, 1, _label(month))
        for column, run in enumerate(runs.values(), start=2):
            number_cell(summary, row, column, run.statement.months[index].closing_cash)
    row += 2
    facts = [
        ("Final closing cash", lambda run: run.statement.months[-1].closing_cash),
        ("Lowest closing cash", lambda run: min(m.closing_cash for m in run.statement.months)),
    ]
    for label, value in facts:
        summary.cell(row, 1, label).font = bold
        for column, run in enumerate(runs.values(), start=2):
            number_cell(summary, row, column, value(run), font=bold, fill=cash_fill)
        summary.cell(row, 1).fill = cash_fill
        row += 1
    summary.cell(row, 1, "Month of lowest closing cash").font = bold
    for column, run in enumerate(runs.values(), start=2):
        low = min(run.statement.months, key=lambda m: m.closing_cash)
        summary.cell(row, column, _label(low.month)).alignment = right
    row += 1
    summary.cell(row, 1, "Months with closing cash below zero").font = bold
    for column, run in enumerate(runs.values(), start=2):
        below = [_label(m.month) for m in run.statement.months if m.closing_cash < 0]
        cell = summary.cell(row, column, ", ".join(below) or "none")
        cell.alignment = Alignment(horizontal="right", wrap_text=True)
        if below:
            cell.font = Font(bold=True, color="C00000")
    row += 1
    summary.cell(row, 1, "Difference to base case, final closing cash").font = bold
    for column, run in enumerate(runs.values(), start=2):
        number_cell(summary, row, column,
                    run.statement.months[-1].closing_cash - base.statement.months[-1].closing_cash)
    row += 3

    scenario_names = [name for name in runs if name != BASE]
    if scenario_names:
        summary.cell(row, 1, "What each scenario changes").font = bold
        row += 1
        for column, text in enumerate(("Scenario", "Line", "Change", "From", "To", "Note",
                                       "Effect over 12 months"), start=1):
            cell = summary.cell(row, column, text)
            cell.font, cell.fill = white, head_fill
        for name in scenario_names:
            effect = {}
            for month in runs[name].statement.months:
                for line in month.lines:
                    if line.assumption_overlay:
                        effect[line.line_id] = effect.get(line.line_id, 0) + line.assumption_overlay
            rules = [r for r in inputs.rules if r.scenario == name]
            for rule in rules:
                row += 1
                change = f"{rule.percent:+g}%" if rule.percent is not None else f"{rule.amount:+,.0f} per month"
                values = (name, definitions[rule.line_id].label, change,
                          _label(rule.from_month) if rule.from_month else "all months",
                          _label(rule.to_month) if rule.to_month else "", rule.note)
                for column, value in enumerate(values, start=1):
                    summary.cell(row, column, value)
                same_line = [r for r in rules if r.line_id == rule.line_id]
                if len(same_line) == 1:
                    number_cell(summary, row, 7, effect.get(rule.line_id, 0))
            legacy = sorted({a.line_id for a in inputs.assumptions if a.scenario_id == name})
            for line_id in legacy:
                row += 1
                for column, value in enumerate((name, definitions[line_id].label, "amounts per month"), start=1):
                    summary.cell(row, column, value)
                number_cell(summary, row, 7, effect.get(line_id, 0))
            row += 1
            summary.cell(row, 1, name).font = bold
            summary.cell(row, 2, "Total effect on final closing cash").font = bold
            number_cell(summary, row, 7, sum(effect.values()), font=bold, fill=total_fill)
            row += 1
    row += 1
    summary.cell(row, 1, "Percentages are taken from each line's base-case figure for that month. "
                         "The base case contains no scenario.").font = italic
    summary.column_dimensions["A"].width = 44
    for column in range(2, 8):
        summary.column_dimensions[get_column_letter(column)].width = 18 if column < 6 else 34
    summary.column_dimensions["B"].width = 40 if scenario_names else 18
    summary.column_dimensions["G"].width = 22

    # --- one statement per scenario -------------------------------------------------
    def actual(line_id, month):
        return inputs.actuals.get(line_id, {}).get(month)

    def write_statement(sheet, name, run):
        by_month = {m.month: m for m in run.statement.months}
        lines = run.statement.months[0].lines
        sheet["A1"] = f"12-month cash forecast ({currency}): {_title(name)}"
        sheet["A1"].font = Font(bold=True, size=14)
        sheet["A2"] = (f"Forecast from {_label(inputs.origin_month)} (last month of actuals). Model: {models}. "
                       f"Data: {run.data_kind}. Opening cash + net cash flow = closing cash, checked to the cent.")
        header_row, kind_row = 4, 5
        sheet.cell(header_row, 1, "Cash-flow line").font = white
        sheet.cell(header_row, 2, "Treatment").font = white
        for offset, month in enumerate(months):
            cell = sheet.cell(header_row, 3 + offset, _label(month))
            cell.font, cell.alignment = white, right
            kind = sheet.cell(kind_row, 3 + offset, "Actual" if month in actual_months else "Forecast")
            kind.alignment, kind.font = right, italic
        for column in range(1, 3 + len(months)):
            sheet.cell(header_row, column).fill = head_fill

        def put(row, label, values, *, font=None, fill=None, treatment="", line_id=None):
            sheet.cell(row, 1, label).font = font or Font()
            sheet.cell(row, 2, treatment)
            sources = treatments[line_id][1] if line_id else None
            for offset, value in enumerate(values):
                month = months[offset]
                shade = fill or (actual_fill if month in actual_months else None)
                if sources and month not in actual_months:
                    shade = source_fill[sources[offset - len(actual_months)]]
                number_cell(sheet, row, 3 + offset, value, font=font, fill=shade)
            if fill:
                sheet.cell(row, 1).fill = fill
                sheet.cell(row, 2).fill = fill

        def section_total(section, month):
            if month in by_month:
                return by_month[month].sections[section]
            if not complete:
                return None
            return sum(actual(line.line_id, month) or 0.0 for line in schema.lines if line.section == section)

        def net(month):
            if month in by_month:
                return by_month[month].net_cash_movement
            totals = [section_total(section, month) for section in SECTIONS]
            return None if None in totals else sum(totals)

        row = 6
        put(row, "Opening cash", [by_month[m].opening_cash if m in by_month
                                  else inputs.balances["opening_cash"].get(m) for m in months],
            font=bold, fill=cash_fill)
        row += 2
        for section in SECTIONS:
            sheet.cell(row, 1, SECTION_TITLES[section]).font = bold
            row += 1
            for position, line in enumerate(lines):
                definition = definitions[line.line_id]
                if definition.section != section:
                    continue
                values = [by_month[m].lines[position].point if m in by_month else actual(line.line_id, m)
                          for m in months]
                put(row, "  " + definition.label, values, treatment=treatments[line.line_id][0],
                    line_id=line.line_id)
                row += 1
            put(row, f"Net cash from {section} activities", [section_total(section, m) for m in months],
                font=bold, fill=total_fill)
            row += 2
        put(row, "Net cash flow", [net(m) for m in months], font=bold, fill=total_fill)
        row += 1
        put(row, "Closing cash", [by_month[m].closing_cash if m in by_month
                                  else inputs.balances["closing_cash"].get(m) for m in months],
            font=bold, fill=cash_fill)
        row += 2
        sheet.cell(row, 1, "Where each forecast figure comes from").font = bold
        legend = ((None, "No shading: projected by the model"),
                  (known_fill, "Your known amount (a known line, or it replaced the model's figure)"),
                  (on_top_fill, "The model's figure plus your known amount on top"),
                  (actual_fill, "Your actuals"))
        for fill, text in legend:
            row += 1
            if fill:
                sheet.cell(row, 1).fill = fill
            sheet.cell(row, 2, text).font = italic
        sheet.column_dimensions["A"].width = 48
        sheet.column_dimensions["B"].width = 44
        for offset in range(len(months)):
            sheet.column_dimensions[get_column_letter(3 + offset)].width = 13
        sheet.freeze_panes = sheet.cell(6, 3)

    for name, run in runs.items():
        write_statement(book.create_sheet(names[name]), name, run)

    # --- where each forecast figure comes from ---------------------------------------
    detail = book.create_sheet("Where numbers come from")
    detail.append(["Scenario", "Month", "Line", "Source", "Model forecast", "Known amounts",
                   "Scenario change", "Total", "Model", "Note"])
    for cell in detail[1]:
        cell.font = bold
    for name, run in runs.items():
        for month in run.statement.months:
            for line in month.lines:
                if not (line.base_point or line.known_overlay or line.assumption_overlay):
                    continue
                index = forecast_months.index(month.month)
                source = {"known": "known amount", "on_top": "model + known amount on top",
                          "model": "model"}[treatments[line.line_id][1][index]]
                note = ("known amount replaced the model figure"
                        if (line.line_id, _label(month.month)) in replaced else "")
                detail.append([_title(name), _label(month.month), definitions[line.line_id].label,
                               source, float(line.base_point),
                               float(line.known_overlay), float(line.assumption_overlay), float(line.point),
                               line.model_name or "", note])
    for column, width in zip("ABCDEFGHIJ", (14, 10, 46, 30, 16, 16, 16, 16, 12, 40)):
        detail.column_dimensions[column].width = width
    for cells in detail.iter_rows(min_row=2, min_col=5, max_col=8):
        for cell in cells:
            cell.number_format = NUMBER
    detail.freeze_panes = "A2"
    detail.auto_filter.ref = detail.dimensions

    # --- notes ------------------------------------------------------------------------
    notes = book.create_sheet("Notes")
    rows = [["Model", models], ["Data kind (declared by the user)", base.data_kind],
            ["Last month of actuals", _label(inputs.origin_month)],
            ["Opening cash taken from", inputs.opening_cash_source],
            ["History ties from opening to closing cash",
             {True: "yes", False: "NO, see warnings", None: "not checked"}[inputs.history_ties]],
            ["Scenarios in this workbook", ", ".join(_title(n) for n in runs)], [""],
            ["Left out of the forecast"]]
    left_out = list({d.input_id: d for run in runs.values() for d in run.statement.excluded_inputs
                     if d.reason != "scenario_not_selected"}.values())
    rows += [[f"{d.kind}: {d.input_id}", d.reason] for d in left_out] or [["nothing"]]
    rows += [[""], ["Warnings"]] + ([[w] for w in inputs.warnings] or [["none"]])
    rows += [[""], ["Limits"], ["There is no uncertainty range for closing cash."],
             ["A forecast is not evidence of accuracy. Compare models and review the scenarios."]]
    for values in rows:
        notes.append(values)
    notes.column_dimensions["A"].width = 60
    notes.column_dimensions["B"].width = 50
    book.save(path)
    return path
