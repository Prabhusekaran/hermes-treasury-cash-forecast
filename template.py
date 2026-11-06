"""Write the input template: the worked synthetic example, or an empty one.

The example data lives as plain CSV in examples/synthetic_company so it can be
read and reviewed. An Excel workbook is built from it on request.
"""
from __future__ import annotations

import csv
import shutil
from pathlib import Path

from .loader import TABLES, TemplateError

EXAMPLE_DIR = Path(__file__).resolve().parent / "examples" / "synthetic_company"

CLEAR_NOTE = ("NOTE: these rows belong to the made-up example company. Delete ALL of them, and this note, "
              "before you enter your own.")

INSTRUCTIONS = [
    ["Treasury Cash Forecast: how to fill this template"],
    ["FIRST", "The rows in known_events and scenarios belong to the made-up example company. "
              "Delete all of them before you enter your own, or they will be counted in your forecast."],
    [""],
    ["Method", "Direct cash flow: receipts and payments by type, from opening cash to closing cash."],
    ["Sign rule", "Money coming in is positive. Money going out is negative (write -5000)."],
    ["One currency", "All amounts in this file are in the currency named in 'inputs'."],
    ["Months", "Write months as YYYY-MM, for example 2026-09."],
    [""],
    ["Sheet", "What goes in it"],
    ["inputs", "currency; origin_month = the last month of actuals; data_kind = synthetic, actual or "
               "unspecified. Optional: model = which model projects the forecast lines (empty = ets; you "
               "can also just tell Hermes which model to use); minimum_cash = the balance you do not want "
               "to fall below, so the result lists the months below it. "
               "(opening_cash is only needed here if the history has no closing_cash row.)"],
    ["history", "Your past cash-flow statement and the definition of every line. First row opening_cash, "
                "then one row per line, last row closing_cash; one column per month, ending at origin_month. "
                "Add or remove rows as you need. For each line: line_id = a short name without spaces that "
                "the other sheets refer to; label = the name shown in the result; section = operating, "
                "investing or financing (at least one line in each); treatment = forecast (a model projects "
                "it from this history) or known (you supply the future amounts in known_events; months "
                "without an amount are zero). A line the business never had before is known, because a "
                "model has no history to learn from. "
                "The plugin checks that every month ties: opening cash + all lines = closing cash. "
                "The closing cash of origin_month becomes the opening cash of the forecast. "
                "Forecast lines must have no gaps: 24 months are needed for the default model (ETS), "
                "36 for SARIMA. Large one-offs distort the model; move them to a separate known line."],
    ["known_events", "Future cash movements you already know: rent, tax, loan repayments, capex, the next "
                     "payroll run. One row per payment. effect only matters on a forecast line: "
                     "replace (or empty) = use my amount instead of the model's figure for that month; "
                     "on top = add my amount to the model's figure (for something new that is not in the "
                     "history). If your amounts cover the first months of the forecast, the later months "
                     "keep the model's pattern but start from the level of your last amount; so do not "
                     "put a one-off (a bonus, a back-payment) in such a row, give it its own known line. "
                     "known_from = the month you learned about it; empty = you know it today. "
                     "An amount you learned about AFTER origin_month is left out of the forecast and "
                     "listed on the Summary sheet: a forecast as of a date uses only what was known then. "
                     "To see its effect anyway, add it as a scenario."],
    ["scenarios", "Optional what-if cases, grouped by a scenario name you choose (for example downside). "
                  "percent changes the size of a line: -4 on receipts means 4% lower receipts, 4 on "
                  "supplier payments means 4% higher payments. Type -4, not a percentage-formatted cell. "
                  "Use amount (per month) for a fixed sum, such as a contingency. "
                  "from_month and to_month are optional; empty means all 12 forecast months. "
                  "A payment that arrives late is two amount rows: minus the amount in the month it "
                  "was due, plus the amount in the month it arrives. "
                  "The base case ignores this sheet. Every scenario is run and shown next to the base case."],
    [""],
    ["Before you run", ""],
    ["Clear old rows", "Delete the example's rows in known_events and scenarios (see FIRST above). Later, "
                       "delete a row as soon as it no longer applies: a payment that was cancelled, "
                       "a scenario you dropped."],
    ["Keep sheets in step", "If you add a line in history and it is known, add its future amounts to "
                            "known_events. If you remove a line from history, remove its rows from "
                            "known_events and scenarios too. The check names every row that no longer "
                            "matches, and lists the known amounts and scenarios it found for each line "
                            "so you can spot leftovers."],
    [""],
    ["What you get", "One Excel workbook: a summary comparing closing cash across the base case and every "
                     "scenario; a plain-language page on how the forecast is built; then one cash-flow "
                     "statement per scenario with your last 12 actual months and 12 forecast months, from "
                     "opening cash to closing cash. "
                     "Each forecast month is checked to the cent: opening + net cash flow = closing."],
]


def _example_rows(name, with_example):
    with (EXAMPLE_DIR / f"{name}.csv").open(newline="", encoding="utf-8") as file:
        rows = list(csv.reader(file))
    if with_example:
        return rows
    if name == "inputs":        # keep the keys and explanations, clear the values
        return [rows[0]] + [[r[0], "", r[2]] for r in rows[1:]]
    if name == "history":       # add one column per month and one row per line
        return [["line_id", "label", "section", "treatment", "2024-01", "2024-02"],
                ["opening_cash", "Opening cash", "", "", "", ""],
                ["closing_cash", "Closing cash", "", "", "", ""]]
    return rows[:1]


def _number(text):
    try:
        return float(text) if "." in text else int(text)
    except ValueError:
        return text


def write_template(output_dir, file_format="xlsx", with_example=True):
    """Create the template and return the path the user should edit."""
    if file_format not in ("xlsx", "csv"):
        raise TemplateError(["format must be 'xlsx' or 'csv'."])
    target = Path(str(output_dir)).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    stem = "cash_forecast_example" if with_example else "cash_forecast_template"
    if file_format == "csv":
        folder = target / stem
        if folder.exists() and any(folder.iterdir()):
            raise TemplateError([f"'{folder}' already exists and is not empty; choose another folder."])
        folder.mkdir(exist_ok=True)
        if with_example:
            for name in TABLES:
                shutil.copyfile(EXAMPLE_DIR / f"{name}.csv", folder / f"{name}.csv")
        else:
            for name in TABLES:
                with (folder / f"{name}.csv").open("w", newline="", encoding="utf-8") as file:
                    csv.writer(file).writerows(_example_rows(name, False))
        with (folder / "READ_ME_FIRST.txt").open("w", encoding="utf-8") as file:
            file.write("\n".join(" : ".join(row) for row in INSTRUCTIONS) + "\n")
        return folder
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.worksheet.datavalidation import DataValidation
    except ImportError as exc:
        raise TemplateError(["Writing Excel needs the 'openpyxl' package; ask for format 'csv' instead."]) from exc
    path = target / f"{stem}.xlsx"
    if path.exists():
        raise TemplateError([f"'{path}' already exists; choose another folder or delete the file."])
    book = Workbook()
    sheet = book.active
    sheet.title = "instructions"
    red = PatternFill("solid", fgColor="C00000")
    for row in INSTRUCTIONS:
        sheet.append(row)
    sheet["A1"].font = Font(bold=True, size=13)
    for cell in sheet[2]:
        cell.font, cell.fill = Font(bold=True, color="FFFFFF"), red
    for cells in sheet.iter_rows(min_row=2):
        for cell in cells:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.column_dimensions["A"].width = 22
    sheet.column_dimensions["B"].width = 130

    def dropdown(target, choices, cells):
        validation = DataValidation(type="list", formula1='"' + ",".join(choices) + '"', allow_blank=True)
        validation.error, validation.errorTitle = "Pick one of: " + ", ".join(choices), "Not a valid choice"
        target.add_data_validation(validation)
        validation.add(cells)

    for name in TABLES:
        sheet = book.create_sheet(name)
        rows = _example_rows(name, with_example)
        note = with_example and name in ("known_events", "scenarios")
        if note:
            sheet.append([CLEAR_NOTE])
            sheet["A1"].font, sheet["A1"].fill = Font(bold=True, color="FFFFFF"), red
            for column in range(2, len(rows[0]) + 1):
                sheet.cell(1, column).fill = red
        head = 2 if note else 1
        for index, row in enumerate(rows):
            sheet.append(row if index == 0 else [_number(cell) for cell in row])
        for cell in sheet[head]:
            cell.font = Font(bold=True)
        sheet.column_dimensions["A"].width = 24
        last = max(sheet.max_row, head) + 500
        if name == "history":
            sheet.freeze_panes = "E2"
            sheet.column_dimensions["B"].width = 46
            for cell in sheet["A"] + sheet["B"]:
                if cell.value in ("opening_cash", "closing_cash", "Opening cash", "Closing cash"):
                    cell.font = Font(bold=True)
            dropdown(sheet, ("operating", "investing", "financing"), f"C2:C{last}")
            dropdown(sheet, ("forecast", "known"), f"D2:D{last}")
        elif name == "inputs":
            sheet.freeze_panes = "B2"
            sheet.column_dimensions["B"].width = 16
            sheet.column_dimensions["C"].width = 110
            for cells in sheet.iter_rows(min_row=2):
                if cells[0].value == "data_kind":
                    dropdown(sheet, ("synthetic", "actual", "unspecified"), cells[1].coordinate)
                elif cells[0].value == "model":
                    dropdown(sheet, ("ets", "sarima", "baseline", "seasonal_drift", "seasonal_naive",
                                     "previous_month", "chronos_2"), cells[1].coordinate)
        else:
            sheet.freeze_panes = f"B{head + 1}"
            sheet.column_dimensions[sheet.cell(head, sheet.max_column).column_letter].width = 60
            if name == "known_events":
                dropdown(sheet, ("replace", "on top"), f"D{head + 1}:D{last}")
    book.save(path)
    return path
