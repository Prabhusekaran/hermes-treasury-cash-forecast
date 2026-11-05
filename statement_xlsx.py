"""Write the forecast as an Excel cash-flow statement: opening cash to closing cash.

Columns: the last actual months (from the history the user supplied), then the
12 forecast months. Every figure is the engine's; the workbook holds values,
not formulas, so nothing can drift from the reconciled result.
"""
from __future__ import annotations

from .engine.cash_flow_schema import SECTIONS

ACTUAL_MONTHS_SHOWN = 12
SECTION_TITLES = {"operating": "Operating activities", "investing": "Investing activities",
                  "financing": "Financing activities"}
NUMBER = '#,##0;[Red]-#,##0'


def _label(month):
    return f"{month.year:04d}-{month.month:02d}"


def write_statement(path, run, inputs, warnings):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    bold, white = Font(bold=True), Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="1F3864")
    actual_fill = PatternFill("solid", fgColor="EDEDED")
    total_fill = PatternFill("solid", fgColor="DDEBF7")
    cash_fill = PatternFill("solid", fgColor="FFF2CC")

    statement = run.statement
    forecast_months = [m.month for m in statement.months]
    supplied = sorted({month for values in inputs.actuals.values() for month in values
                       if month <= inputs.origin_month})
    actual_months = supplied[-ACTUAL_MONTHS_SHOWN:]
    months = actual_months + forecast_months
    by_month = {m.month: m for m in statement.months}
    lines = statement.months[0].lines
    definitions = {line.line_id: line for line in run.schema.lines}

    book = Workbook()
    sheet = book.active
    sheet.title = "Cash forecast"
    scenario = statement.scenario_id or "base case"
    models = ", ".join(sorted({a["used_model"] for a in run.model_audit})) or "none"
    sheet["A1"] = f"12-month cash forecast ({statement.currency}), {scenario}"
    sheet["A1"].font = Font(bold=True, size=14)
    sheet["A2"] = (f"Forecast from {_label(inputs.origin_month)} (last month of actuals). Model: {models}. "
                   f"Data: {run.data_kind}. Opening cash + net cash flow = closing cash, checked to the cent.")
    header_row, kind_row = 4, 5
    sheet.cell(header_row, 1, "Cash-flow line").font = white
    sheet.cell(header_row, 2, "Treatment").font = white
    for offset, month in enumerate(months):
        column = 3 + offset
        cell = sheet.cell(header_row, column, _label(month))
        cell.font, cell.alignment = white, Alignment(horizontal="right")
        kind = sheet.cell(kind_row, column, "Actual" if month in actual_months else "Forecast")
        kind.alignment = Alignment(horizontal="right")
        kind.font = Font(italic=True)
    for column in range(1, 3 + len(months)):
        sheet.cell(header_row, column).fill = head_fill

    def actual(line_id, month):
        return inputs.actuals.get(line_id, {}).get(month)

    def put(row, label, values, *, font=None, fill=None, treatment=""):
        sheet.cell(row, 1, label).font = font or Font()
        sheet.cell(row, 2, treatment)
        for offset, value in enumerate(values):
            cell = sheet.cell(row, 3 + offset, None if value is None else float(value))
            cell.number_format = NUMBER
            if font:
                cell.font = font
            if fill:
                cell.fill = fill
            elif months[offset] in actual_months:
                cell.fill = actual_fill
        if fill:
            sheet.cell(row, 1).fill = fill
            sheet.cell(row, 2).fill = fill

    complete = all(inputs.actuals.get(l.line_id) for l in run.schema.lines if l.treatment != "assumption")

    def section_total(section, month):
        if month in by_month:
            return by_month[month].sections[section]
        if not complete:
            return None
        return sum(actual(l.line_id, month) or 0.0 for l in run.schema.lines if l.section == section)

    def net(month):
        if month in by_month:
            return by_month[month].net_cash_movement
        totals = [section_total(s, month) for s in SECTIONS]
        return None if None in totals else sum(totals)

    row = 6
    opening = [by_month[m].opening_cash if m in by_month else inputs.balances["opening_cash"].get(m) for m in months]
    put(row, "Opening cash", opening, font=bold, fill=cash_fill)
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
            put(row, "  " + definition.label, values, treatment=definition.treatment)
            row += 1
        put(row, f"Net cash from {section} activities", [section_total(section, m) for m in months],
            font=bold, fill=total_fill)
        row += 2
    put(row, "Net cash flow", [net(m) for m in months], font=bold, fill=total_fill)
    row += 1
    closing = [by_month[m].closing_cash if m in by_month else inputs.balances["closing_cash"].get(m) for m in months]
    put(row, "Closing cash", closing, font=bold, fill=cash_fill)
    row += 2
    sheet.cell(row, 1, "Treatment: forecast = projected by the model; known = from your schedule; "
                       "assumption = applied only in a scenario. Grey columns are your actuals.").font = Font(italic=True)

    sheet.column_dimensions["A"].width = 48
    sheet.column_dimensions["B"].width = 12
    for offset in range(len(months)):
        sheet.column_dimensions[get_column_letter(3 + offset)].width = 13
    sheet.freeze_panes = sheet.cell(6, 3)

    # line detail: where each forecast figure comes from
    detail = book.create_sheet("Where each number comes from")
    detail.append(["Month", "Line", "Treatment", "Model forecast", "Known schedule", "Scenario assumption",
                   "Total", "Model", "Known items", "Assumptions"])
    for cell in detail[1]:
        cell.font = bold
    for month in statement.months:
        for line in month.lines:
            if not (line.base_point or line.known_overlay or line.assumption_overlay):
                continue
            detail.append([_label(month.month), definitions[line.line_id].label,
                           definitions[line.line_id].treatment, float(line.base_point),
                           float(line.known_overlay), float(line.assumption_overlay), float(line.point),
                           line.model_name or "", len(line.event_ids), len(line.assumption_ids)])
    for column, width in zip("ABCDEFGHIJ", (10, 46, 12, 16, 16, 20, 16, 14, 12, 12)):
        detail.column_dimensions[column].width = width
    for cells in detail.iter_rows(min_row=2, min_col=4, max_col=7):
        for cell in cells:
            cell.number_format = NUMBER
    detail.freeze_panes = "A2"

    # notes
    notes = book.create_sheet("Notes")
    rows = [["Scenario", scenario], ["Model", models], ["Data kind (declared by the user)", run.data_kind],
            ["Last month of actuals", _label(inputs.origin_month)],
            ["Opening cash taken from", inputs.opening_cash_source],
            ["History ties from opening to closing cash",
             {True: "yes", False: "NO, see warnings", None: "not checked"}[inputs.history_ties]], [""],
            ["Left out of the forecast"]]
    left_out = [d for d in statement.excluded_inputs if d.reason != "scenario_not_selected"]
    rows += [[f"{d.kind}: {d.input_id}", d.reason] for d in left_out] or [["nothing"]]
    rows += [[""], ["Warnings"]] + ([[w] for w in warnings] or [["none"]])
    rows += [[""], ["Limits"], ["There is no uncertainty range for closing cash."],
             ["A forecast is not evidence of accuracy. Compare models and review the assumptions."]]
    for values in rows:
        notes.append(values)
    notes.column_dimensions["A"].width = 60
    notes.column_dimensions["B"].width = 50
    book.save(path)
    return path
