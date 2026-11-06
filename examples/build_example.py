"""Rebuild the synthetic example company (made-up data, fixed seed).

Run from the repository root:  python examples/build_example.py
Standard library only. The CSV files it writes are what the plugin ships.

The company is a mid-sized manufacturer reporting a direct cash-flow statement:
receipts and payments by type, from opening cash to closing cash every month.
"""
import csv
import random
from datetime import date
from pathlib import Path

OUT = Path(__file__).resolve().parent / "synthetic_company"
ORIGIN = date(2026, 9, 1)          # last month of actuals
CLOSING_CASH_AT_ORIGIN = 1_180_000
SEASON = (0.90, 0.92, 0.98, 1.01, 1.03, 1.06, 1.08, 1.02, 1.01, 1.00, 1.03, 1.12)
QUARTER_END = (3, 6, 9, 12)
QUARTER_START = (1, 4, 7, 10)

LINES = [
    # line_id, label, section, treatment
    ("receipts_domestic", "Receipts from customers, domestic", "operating", "forecast"),
    ("receipts_export", "Receipts from customers, export", "operating", "forecast"),
    ("other_receipts", "Other operating receipts (refunds, grants)", "operating", "forecast"),
    ("suppliers_materials", "Payments to suppliers, materials", "operating", "forecast"),
    ("suppliers_services", "Payments to suppliers, services and logistics", "operating", "forecast"),
    ("net_salaries", "Net salaries", "operating", "forecast"),
    ("payroll_taxes", "Payroll taxes and social security", "operating", "forecast"),
    ("rent_and_leases", "Rent and leases", "operating", "known"),
    ("utilities_insurance", "Utilities and insurance", "operating", "forecast"),
    ("marketing_travel_it", "Marketing, travel and IT", "operating", "forecast"),
    ("vat_payments", "VAT paid to the tax office (net)", "operating", "forecast"),
    ("corporate_tax", "Corporate tax instalments", "operating", "known"),
    ("management_reserve", "Management contingency (scenarios only)", "operating", "known"),
    ("capex", "Purchase of equipment and software", "investing", "known"),
    ("asset_disposals", "Proceeds from sale of assets", "investing", "known"),
    ("interest_received", "Interest received", "investing", "known"),
    ("loan_drawdowns", "Loan drawdowns", "financing", "known"),
    ("loan_repayments", "Loan repayments", "financing", "known"),
    ("interest_paid", "Interest paid", "financing", "known"),
    ("dividends", "Dividends paid", "financing", "known"),
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
        winter = 1.25 if month.month in (12, 1, 2) else 0.9
        add = lambda line, value: history[line].append(round(value, 2))
        add("receipts_domestic", 980_000 * 1.004 ** i * season * rng.gauss(1, 0.025))
        add("receipts_export", 470_000 * 1.007 ** i * (2 - season) * rng.gauss(1, 0.04))
        add("other_receipts", 14_000 * rng.gauss(1, 0.25))
        add("suppliers_materials", -520_000 * 1.004 ** i * (0.7 + 0.3 * season) * rng.gauss(1, 0.02))
        add("suppliers_services", -175_000 * 1.004 ** i * rng.gauss(1, 0.03))
        add("net_salaries", -262_000 * 1.002 ** i)
        add("payroll_taxes", -158_000 * 1.002 ** i)
        add("rent_and_leases", -58_000)
        add("utilities_insurance", -41_000 * winter * rng.gauss(1, 0.05))
        add("marketing_travel_it", -96_000 * 1.003 ** i * (1.2 if month.month in (3, 9) else 1.0) * rng.gauss(1, 0.06))
        add("vat_payments", -88_000 * 1.004 ** i * season * rng.gauss(1, 0.05))
        add("corporate_tax", -120_000 if month.month in QUARTER_END else 0)
        add("management_reserve", 0)
        add("capex", -280_000 if i in (4, 16, 28) else 0)
        add("asset_disposals", 65_000 if i == 20 else 0)
        add("interest_received", 4_500 if month.month in QUARTER_END else 0)
        add("loan_drawdowns", 600_000 if i == 3 else 0)
        add("loan_repayments", -75_000 if month.month in QUARTER_START else 0)
        add("interest_paid", -21_000 if month.month in QUARTER_START else 0)
        add("dividends", -300_000 if month.month == 6 else 0)

    # Opening and closing cash for every month, tied to the lines above.
    net = [round(sum(history[line[0]][i] for line in LINES), 2) for i in range(36)]
    closing = [0.0] * 36
    closing[-1] = float(CLOSING_CASH_AT_ORIGIN)
    for i in range(34, -1, -1):
        closing[i] = round(closing[i + 1] - net[i + 1], 2)
    opening = [round(closing[0] - net[0], 2)] + closing[:-1]

    write("inputs.csv", ["key", "value", "explanation"], [
        ["currency", "EUR", "Three-letter currency code. One currency per file."],
        ["origin_month", ym(ORIGIN), "The last month of actuals. The forecast covers the 12 months after it."],
        ["data_kind", "synthetic", "synthetic = made-up data, actual = real company data, unspecified = not stated."],
        ["model", "ets", "Optional. ets (default), sarima, baseline, seasonal_drift, seasonal_naive, "
                         "previous_month or chronos_2. Empty = ets."],
        ["minimum_cash", 250_000, "Optional. The cash balance you do not want to fall below. "
                                  "The result lists the months below it."],
    ])
    write("history.csv", ["line_id", "label", "section", "treatment"] + [ym(m) for m in months],
          [["opening_cash", "Opening cash", "", ""] + opening]
          + [list(line) + history[line[0]] for line in LINES]
          + [["closing_cash", "Closing cash", "", ""] + closing])

    events = []
    for i in range(1, 13):
        month = add_months(ORIGIN, i)
        if i <= 2:      # the next two payroll runs are already fixed; later months come from the model
            events.append(["net_salaries", ym(month), -286_500 if i == 1 else -288_000, "replace", "",
                           "Payroll run already approved"])
            events.append(["payroll_taxes", ym(month), -172_800 if i == 1 else -173_700, "replace", "",
                           "Payroll taxes on the approved payroll run"])
        events.append(["rent_and_leases", ym(month), -60_000, "", "", "Office and warehouse lease"])
        if month.month in QUARTER_END:
            events.append(["corporate_tax", ym(month), -130_000, "", "", "Assessed quarterly instalment"])
            events.append(["interest_received", ym(month), 4_000, "", "", "Interest on deposits"])
        if month.month in QUARTER_START:
            events.append(["loan_repayments", ym(month), -75_000, "", "", "Term loan, scheduled repayment"])
            events.append(["interest_paid", ym(month), -18_000, "", "", "Term loan interest"])
    events += [
        ["capex", "2026-11", -350_000, "", "", "Approved production equipment"],
        ["capex", "2027-05", -220_000, "", "", "Approved warehouse fit-out"],
        ["asset_disposals", "2027-02", 40_000, "", "", "Sale of old forklift fleet, contract signed"],
        ["dividends", "2027-06", -320_000, "", "", "Dividend proposed for the annual meeting"],
        ["receipts_export", "2027-01", 45_000, "on top", "", "New export contract, not in the historical run-rate"],
        ["receipts_export", "2027-02", 45_000, "on top", "", "New export contract, not in the historical run-rate"],
        ["receipts_export", "2027-03", 45_000, "on top", "", "New export contract, not in the historical run-rate"],
        ["loan_drawdowns", "2027-03", 1_000_000, "", "2026-11",
         "Refinancing drawdown; only agreed in November 2026, so not known at the forecast date"],
    ]
    write("known_events.csv", ["line_id", "month", "amount", "effect", "known_from", "note"], events)

    write("scenarios.csv", ["scenario", "line_id", "percent", "amount", "from_month", "to_month", "note"], [
        ["downside", "receipts_domestic", -4, "", "", "", "Domestic customers pay less and later"],
        ["downside", "receipts_export", -8, "", "", "", "Weaker export demand"],
        ["downside", "suppliers_materials", 4, "", "2027-01", "", "Material prices rise from January"],
        ["downside", "management_reserve", "", -20_000, "", "", "Contingency for unplanned costs"],
        ["upside", "receipts_domestic", 3, "", "2027-01", "", "Price increase from January 2027"],
        ["upside", "receipts_export", 5, "", "2027-01", "", "New distributor from January 2027"],
        ["with_refinancing", "loan_drawdowns", "", 1_000_000, "2027-03", "2027-03",
         "Refinancing drawdown, if it is agreed as expected"],
        ["customer_pays_late", "receipts_export", "", -300_000, "2027-06", "2027-06",
         "Largest export customer pays one month late: missing in June"],
        ["customer_pays_late", "receipts_export", "", 300_000, "2027-07", "2027-07",
         "Largest export customer pays one month late: arrives in July"],
    ])


if __name__ == "__main__":
    main()
