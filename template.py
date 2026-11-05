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

INSTRUCTIONS = [
    ["Treasury Cash Forecast: how to fill this template"],
    [""],
    ["Method", "Direct cash flow: receipts and payments by type, from opening cash to closing cash."],
    ["Sign rule", "Money coming in is positive. Money going out is negative (write -5000)."],
    ["One currency", "All amounts in this file are in the currency named in 'inputs'."],
    ["Months", "Write months as YYYY-MM, for example 2026-09."],
    [""],
    ["Sheet", "What goes in it"],
    ["inputs", "currency; origin_month = the last month of actuals; data_kind = synthetic, actual or "
               "unspecified. (opening_cash is only needed here if the history has no closing_cash row.)"],
    ["lines", "One row per cash-flow line; add as many as you need. line_id is a short name without "
              "spaces that the other sheets refer to. section = operating, investing or financing. "
              "treatment = forecast (a model projects it from history) or known (you supply the future "
              "amounts in known_events; months without an amount are zero). A line the business never "
              "had before is known, because a model has no history to learn from. "
              "At least one line per section. Every line_id here needs a row in history."],
    ["history", "Your past cash-flow statement, one column per month, ending at origin_month. "
                "First row opening_cash, then one row per line, last row closing_cash. "
                "The plugin checks that every month ties: opening cash + all lines = closing cash. "
                "The closing cash of origin_month becomes the opening cash of the forecast. "
                "Forecast lines must have no gaps: 24 months are needed for the default model (ETS), "
                "36 for SARIMA. For forecast lines, one-off items distort the model; move large "
                "one-offs to a separate known line."],
    ["known_events", "Future cash movements you already know: rent, tax, loan repayments, capex, the next "
                     "payroll run. One row per payment. effect only matters on a forecast line: "
                     "replace (or empty) = use my amount instead of the model's figure for that month; "
                     "on top = add my amount to the model's figure (for something new that is not in the "
                     "history). known_from = the month you learned about it; empty = you know it today."],
    ["scenarios", "Optional what-if cases, grouped by a scenario name you choose (for example downside). "
                  "percent changes the size of a line: -4 on receipts means 4% lower receipts, 4 on "
                  "supplier payments means 4% higher payments. Type -4, not a percentage-formatted cell. "
                  "Use amount (per month) for a fixed sum, such as a contingency. "
                  "from_month and to_month are optional; empty means all 12 forecast months. "
                  "The base case ignores this sheet. Every scenario is run and shown next to the base case."],
    ["Keeping sheets in step", "If you add a line: add it to lines, add its row to history, and if it is known, "
                               "add its future amounts to known_events. If you remove a line: remove its rows "
                               "from history, known_events and scenarios too. The check tells you exactly "
                               "which rows no longer match."],
    [""],
    ["What you get", "One Excel workbook: a summary comparing closing cash across the base case and every "
                     "scenario, then one cash-flow statement per scenario with your last 12 actual months "
                     "and 12 forecast months, from opening cash to closing cash. "
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
        return [["line_id", "2024-01", "2024-02"], ["opening_cash", "", ""], ["closing_cash", "", ""]]
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
        from openpyxl.styles import Font
    except ImportError as exc:
        raise TemplateError(["Writing Excel needs the 'openpyxl' package; ask for format 'csv' instead."]) from exc
    path = target / f"{stem}.xlsx"
    if path.exists():
        raise TemplateError([f"'{path}' already exists; choose another folder or delete the file."])
    book = Workbook()
    sheet = book.active
    sheet.title = "instructions"
    for row in INSTRUCTIONS:
        sheet.append(row)
    sheet["A1"].font = Font(bold=True, size=13)
    sheet.column_dimensions["A"].width = 16
    sheet.column_dimensions["B"].width = 120
    for name in TABLES:
        sheet = book.create_sheet(name)
        rows = _example_rows(name, with_example)
        for index, row in enumerate(rows):
            sheet.append(row if index == 0 else [_number(cell) for cell in row])
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = "B2"
        sheet.column_dimensions["A"].width = 24
    book.save(path)
    return path
