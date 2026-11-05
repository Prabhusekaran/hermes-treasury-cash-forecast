"""Rebuild the synthetic example company (made-up data, fixed seed).

Run from the repository root:  python examples/build_example.py
Standard library only. The CSV files it writes are what the plugin ships.
"""
import csv
import random
from datetime import date
from pathlib import Path

OUT = Path(__file__).resolve().parent / "synthetic_company"
ORIGIN = date(2026, 9, 1)          # last month of actuals
OPENING_CASH = 650_000
SEASON = (0.90, 0.92, 0.98, 1.01, 1.03, 1.06, 1.08, 1.02, 1.01, 1.00, 1.03, 1.12)

LINES = [
    # line_id, label, section, treatment
    ("customer_receipts", "Customer receipts", "operating", "forecast"),
    ("supplier_payments", "Supplier payments", "operating", "forecast"),
    ("operating_expenses", "Other operating expenses (travel, marketing, IT)", "operating", "forecast"),
    ("payroll", "Payroll and social security", "operating", "known"),
    ("rent_and_leases", "Rent and leases", "operating", "known"),
    ("corporate_tax", "Corporate tax instalments", "operating", "known"),
    ("capex", "Capital expenditure", "investing", "known"),
    ("loan_repayments", "Loan repayments and drawdowns", "financing", "known"),
    ("interest_paid", "Interest paid", "financing", "known"),
    ("dividends", "Dividends", "financing", "known"),
    ("management_reserve", "Management contingency", "operating", "assumption"),
]


def add_months(month, count):
    index = month.year * 12 + month.month - 1 + count
    return date(index // 12, index % 12 + 1, 1)


def ym(month):
    return f"{month.year:04d}-{month.month:02d}"


def write(name, header, rows):
    with (OUT / name).open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(header)
        writer.writerows(rows)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(2026)
    months = [add_months(ORIGIN, i - 35) for i in range(36)]
    history = {line[0]: [] for line in LINES}
    for i, month in enumerate(months):
        season = SEASON[month.month - 1]
        history["customer_receipts"].append(round(1_450_000 * 1.005 ** i * season * rng.gauss(1, 0.025), 2))
        history["supplier_payments"].append(round(-760_000 * 1.004 ** i * (0.7 + 0.3 * season) * rng.gauss(1, 0.02), 2))
        history["operating_expenses"].append(round(-200_000 * 1.003 ** i * (1.25 if month.month == 12 else 1.0) * rng.gauss(1, 0.04), 2))
        history["payroll"].append(round(-420_000 * 1.002 ** i, 2))
        history["rent_and_leases"].append(-58_000)
        history["corporate_tax"].append(-120_000 if month.month in (3, 6, 9, 12) else 0)
        history["capex"].append(-280_000 if i in (4, 16, 28) else 0)
        history["loan_repayments"].append(-75_000 if month.month in (1, 4, 7, 10) else 0)
        history["interest_paid"].append(-21_000 if month.month in (1, 4, 7, 10) else 0)
        history["dividends"].append(-300_000 if month.month == 6 else 0)
        history["management_reserve"].append(0)

    write("inputs.csv", ["key", "value", "explanation"], [
        ["currency", "EUR", "Three-letter currency code. One currency per file."],
        ["origin_month", ym(ORIGIN), "The last month of actuals. The forecast covers the 12 months after it."],
        ["opening_cash", OPENING_CASH, "Cash balance at the end of origin_month."],
        ["data_kind", "synthetic", "synthetic = made-up data, actual = real company data, unspecified = not stated."],
    ])
    write("lines.csv", ["line_id", "label", "section", "treatment"], LINES)
    write("history.csv", ["line_id"] + [ym(m) for m in months],
          [[line[0]] + history[line[0]] for line in LINES])

    events = []
    for i in range(1, 13):
        month = add_months(ORIGIN, i)
        events.append(["payroll", ym(month), -452_000 if month.year == 2026 else -466_000, "",
                       "Contracted payroll; pay rise from January 2027"])
        events.append(["rent_and_leases", ym(month), -60_000, "", "Office lease"])
        if month.month in (3, 6, 9, 12):
            events.append(["corporate_tax", ym(month), -130_000, "", "Assessed quarterly instalment"])
        if month.month in (1, 4, 7, 10):
            events.append(["loan_repayments", ym(month), -75_000, "", "Term loan, scheduled repayment"])
            events.append(["interest_paid", ym(month), -18_000, "", "Term loan interest"])
    events += [
        ["capex", "2026-11", -350_000, "", "Approved production equipment"],
        ["capex", "2027-05", -220_000, "", "Approved warehouse fit-out"],
        ["dividends", "2027-06", -320_000, "", "Dividend proposed for the annual meeting"],
        ["customer_receipts", "2027-01", 45_000, "", "New customer contract, not in the historical run-rate"],
        ["customer_receipts", "2027-02", 45_000, "", "New customer contract, not in the historical run-rate"],
        ["customer_receipts", "2027-03", 45_000, "", "New customer contract, not in the historical run-rate"],
        ["loan_repayments", "2027-03", 1_000_000, "2026-11",
         "Refinancing drawdown; only agreed in November 2026, so not known at the forecast date"],
    ]
    write("known_events.csv", ["line_id", "month", "amount", "known_from", "note"], events)

    assumptions = []
    for i in range(1, 13):
        month = ym(add_months(ORIGIN, i))
        assumptions.append(["downside", "customer_receipts", month, -60_000, "", "Customers pay more slowly"])
        assumptions.append(["downside", "management_reserve", month, -20_000, "", "Contingency for unplanned costs"])
        if i >= 4:
            assumptions.append(["upside", "customer_receipts", month, 40_000, "", "Price increase from January 2027"])
    write("assumptions.csv", ["scenario", "line_id", "month", "amount", "available_from", "note"], assumptions)


if __name__ == "__main__":
    main()
