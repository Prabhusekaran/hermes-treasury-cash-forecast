"""Small helpers shared by the other modules: file locations, dates and CSV files."""

import csv
from datetime import date
from pathlib import Path

# src/treasury_lab/common.py -> parents[2] is the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_DIR = PROJECT_ROOT / "data" / "synthetic"
OUTPUT_DIR = PROJECT_ROOT / "outputs"

HISTORY_FILE = SYNTHETIC_DIR / "monthly_cash_flow_history.csv"
EVENTS_FILE = SYNTHETIC_DIR / "known_events.csv"


def add_months(month, count):
    """Return the first day of the month that is `count` months after `month`."""
    index = month.year * 12 + (month.month - 1) + count
    return date(index // 12, index % 12 + 1, 1)


def month_range(start, count):
    """Return `count` consecutive first-of-month dates beginning at `start`."""
    return [add_months(start, i) for i in range(count)]


def parse_month(text):
    """Turn '2026-10' or '2026-10-01' into date(2026, 10, 1)."""
    parts = text.split("-")
    return date(int(parts[0]), int(parts[1]), 1)


def write_csv(path, rows, fieldnames):
    """Write a list of dicts to a CSV file, creating the folder if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_csv_rows(path):
    """Read a CSV file into a list of dicts (all values are still text)."""
    with open(path, newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def read_history(path=HISTORY_FILE):
    """Read the monthly history: `month` becomes a date, every other column a float."""
    history = []
    for raw in read_csv_rows(path):
        row = {"month": parse_month(raw["month"])}
        for name, value in raw.items():
            if name != "month":
                row[name] = float(value)
        history.append(row)
    return history
