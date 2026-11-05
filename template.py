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
    ["Sign rule", "Money coming in is positive. Money going out is negative (write -5000)."],
    ["One currency", "All amounts in this file are in the currency named in 'inputs'."],
    ["Months", "Write months as YYYY-MM, for example 2026-09."],
    [""],
    ["Sheet", "What goes in it"],
    ["inputs", "The four general inputs. currency; origin_month = the last month of actuals; "
               "opening_cash = the cash balance at the end of origin_month; "
               "data_kind = synthetic, actual or unspecified."],
    ["lines", "One row per cash-flow line; add as many as you need. line_id is a short name without "
              "spaces that the other sheets refer to. section = operating, investing or financing. "
              "treatment = forecast (a model projects it from history), known (you supply the schedule) "
              "or assumption (only applied when you pick a scenario). At least one line per section."],
    ["history", "Past actuals: one row per line, one column per month, ending at origin_month. "
                "Only FORECAST lines are used by the model, and for those there must be no gaps. "
                "Rows for known and assumption lines are optional and kept for reference only. "
                "For forecast lines use recurring cash flow: leave out one-offs and anything you list "
                "as a known event. 24 months are needed for the default model (ETS); 36 for SARIMA."],
    ["known_events", "Future cash movements you already know: payroll, rent, tax, loan repayments, capex. "
                     "One row per payment. known_from = the month you learned about it; leave it empty "
                     "if you know it today. On a forecast line the amount is added on top of the model forecast."],
    ["assumptions", "Optional what-if adjustments, grouped by a scenario name you choose (for example "
                    "downside). The base forecast ignores this sheet. When you ask for a scenario, its rows "
                    "are added to the base forecast, so you can compare base and downside cash."],
    [""],
    ["What you get", "12 months: every line, the section totals, net cash movement, opening and closing "
                     "cash. Each month is checked to the cent: opening + movement = closing."],
]


def _example_rows(name, with_example):
    with (EXAMPLE_DIR / f"{name}.csv").open(newline="", encoding="utf-8") as file:
        rows = list(csv.reader(file))
    if with_example:
        return rows
    if name == "inputs":        # keep the keys and explanations, clear the values
        return [rows[0]] + [[r[0], "", r[2]] for r in rows[1:]]
    if name == "history":
        return [["line_id", "2024-01", "2024-02"]]   # add one column per month
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
