"""Rebuild the synthetic example hotel (made-up data, fixed seed).

Run from the repository root:  python examples/build_hotel_example.py
Standard library only. The CSV files it writes are what the plugin ships.

The company is one invented 110-room hotel reporting a direct cash-flow
statement. Its cash is strongly seasonal: it earns most of its money from May
to September and pays its fixed costs, its insurance and its refurbishment in
the quiet months. The question the example answers is the one a hotel asks
every autumn: does the cash last through the winter?

No figure comes from a real hotel or from any company's data.
"""
import csv
import random
from datetime import date
from pathlib import Path

OUT = Path(__file__).resolve().parent / "synthetic_hotel"
ORIGIN = date(2026, 10, 1)          # last month of actuals: the end of the season
CLOSING_CASH_AT_ORIGIN = 690_000
MINIMUM_CASH = 150_000
# Share of a year's business falling in each calendar month, January first (average = 1).
ROOMS = (0.55, 0.60, 0.75, 0.95, 1.15, 1.30, 1.50, 1.50, 1.25, 1.00, 0.70, 0.75)
EVENTS = (0.60, 0.80, 1.10, 1.20, 1.30, 1.10, 0.50, 0.40, 1.30, 1.40, 1.20, 1.10)
QUARTER_END = (3, 6, 9, 12)
QUARTER_START = (1, 4, 7, 10)

LINES = [
    # line_id, label, section, treatment
    ("rooms_direct", "Room receipts, direct bookings", "operating", "forecast"),
    ("rooms_channels", "Room receipts, online booking channels", "operating", "forecast"),
    ("food_beverage", "Food and beverage receipts", "operating", "forecast"),
    ("meetings_events", "Meetings and events receipts", "operating", "forecast"),
    ("other_receipts", "Other receipts (parking, spa, minibar)", "operating", "forecast"),
    ("channel_commissions", "Commissions paid to booking channels", "operating", "forecast"),
    ("card_fees", "Card and payment processing fees", "operating", "forecast"),
    ("food_beverage_suppliers", "Payments to food and beverage suppliers", "operating", "forecast"),
    ("net_salaries", "Net salaries, permanent staff", "operating", "forecast"),
    ("payroll_taxes", "Payroll taxes and social security", "operating", "forecast"),
    ("seasonal_staff", "Seasonal and agency staff", "operating", "forecast"),
    ("energy_utilities", "Energy and utilities", "operating", "forecast"),
    ("housekeeping_supplies", "Laundry, cleaning and guest supplies", "operating", "forecast"),
    ("repairs_maintenance", "Repairs and maintenance", "operating", "forecast"),
    ("marketing_software", "Marketing, software and IT", "operating", "forecast"),
    ("vat_payments", "VAT paid to the tax office (net)", "operating", "forecast"),
    ("property_lease", "Property lease", "operating", "known"),
    ("insurance", "Insurance premium (paid once a year)", "operating", "known"),
    ("corporate_tax", "Corporate tax instalments", "operating", "known"),
    ("management_reserve", "Management contingency (scenarios only)", "operating", "known"),
    ("refurbishment", "Room refurbishment and equipment", "investing", "known"),
    ("interest_received", "Interest received", "investing", "known"),
    ("credit_line", "Seasonal credit line (drawn +, repaid -)", "financing", "known"),
    ("loan_repayments", "Loan repayments", "financing", "known"),
    ("interest_paid", "Interest paid", "financing", "known"),
    ("owner_distributions", "Distributions to the owners", "financing", "known"),
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
    rng = random.Random(1108)
    months = [add_months(ORIGIN, i - 35) for i in range(36)]
    history = {line[0]: [] for line in LINES}
    for i, month in enumerate(months):
        rooms = ROOMS[month.month - 1]
        events = EVENTS[month.month - 1]
        growth = 1.003 ** i                     # prices and volumes creep up just under 4% a year
        winter = 1.45 if month.month in (12, 1, 2) else 1.15 if month.month in (11, 3) else 0.85
        add = lambda line, value: history[line].append(round(value, 2))
        direct = 166_000 * growth * rooms * rng.gauss(1, 0.03)
        channels = 136_000 * growth * (0.25 + 0.75 * rooms) * rng.gauss(1, 0.04)
        food = 104_000 * growth * (0.45 + 0.55 * rooms) * rng.gauss(1, 0.03)
        meetings = 36_000 * growth * events * rng.gauss(1, 0.08)
        other = 16_000 * growth * (0.5 + 0.5 * rooms) * rng.gauss(1, 0.06)
        add("rooms_direct", direct)
        add("rooms_channels", channels)
        add("food_beverage", food)
        add("meetings_events", meetings)
        add("other_receipts", other)
        add("channel_commissions", -0.17 * channels * rng.gauss(1, 0.02))
        add("card_fees", -0.013 * (direct + channels + food + meetings + other))
        add("food_beverage_suppliers", -0.31 * food * rng.gauss(1, 0.03))
        add("net_salaries", -99_000 * 1.0025 ** i)
        add("payroll_taxes", -60_000 * 1.0025 ** i)
        add("seasonal_staff", -34_000 * growth * max(rooms - 0.7, 0.05) * rng.gauss(1, 0.06))
        add("energy_utilities", -19_500 * growth * winter * rng.gauss(1, 0.05))
        add("housekeeping_supplies", -17_500 * growth * (0.3 + 0.7 * rooms) * rng.gauss(1, 0.04))
        add("repairs_maintenance", -15_500 * growth * rng.gauss(1, 0.15))
        add("marketing_software", -17_500 * growth * (1.3 if month.month in (2, 3, 4) else 0.9) * rng.gauss(1, 0.05))
        add("vat_payments", -0.055 * (direct + channels + food) * rng.gauss(1, 0.04))
        add("property_lease", -88_000 if i < 24 else -90_000)
        add("insurance", -56_000 if month.month == 1 else 0)
        add("corporate_tax", -22_000 if month.month in QUARTER_END else 0)
        add("management_reserve", 0)
        add("refurbishment", {2: -150_000, 3: -90_000, 14: -160_000, 15: -100_000,
                              26: -170_000, 27: -110_000}.get(i, 0))
        add("interest_received", 1_200 if month.month in QUARTER_END else 0)
        add("credit_line", 0)
        add("loan_repayments", -35_000 if month.month in QUARTER_START else 0)
        add("interest_paid", -11_000 if month.month in QUARTER_START else 0)
        add("owner_distributions", -200_000 if month.month == 10 else 0)

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
        ["minimum_cash", MINIMUM_CASH, "Optional. The cash balance you do not want to fall below. "
                                       "The result lists the months below it."],
    ])
    write("history.csv", ["line_id", "label", "section", "treatment"] + [ym(m) for m in months],
          [["opening_cash", "Opening cash", "", ""] + opening]
          + [list(line) + history[line[0]] for line in LINES]
          + [["closing_cash", "Closing cash", "", ""] + closing])

    events = []
    for i in range(1, 13):
        month = add_months(ORIGIN, i)
        if i <= 2:      # the winter rota is fixed, so the next two payroll runs are already known
            events.append(["net_salaries", ym(month), -110_500 if i == 1 else -111_000, "replace", "",
                           "Payroll run from the approved winter rota"])
            events.append(["payroll_taxes", ym(month), -67_200 if i == 1 else -67_500, "replace", "",
                           "Payroll taxes on the approved winter rota"])
        events.append(["property_lease", ym(month), -92_000, "", "", "Lease of the hotel building, indexed"])
        if month.month == 1:
            events.append(["insurance", ym(month), -59_000, "", "", "Annual premium, renewal confirmed"])
        if month.month in QUARTER_END:
            events.append(["corporate_tax", ym(month), -24_000, "", "", "Assessed quarterly instalment"])
            events.append(["interest_received", ym(month), 1_000, "", "", "Interest on deposits"])
        if month.month in QUARTER_START:
            events.append(["loan_repayments", ym(month), -35_000, "", "", "Property loan, scheduled repayment"])
            events.append(["interest_paid", ym(month), -9_500, "", "", "Property loan interest"])
    events += [
        ["refurbishment", "2027-01", -180_000, "", "", "Refurbishment of 24 rooms, first payment, contract signed"],
        ["refurbishment", "2027-02", -120_000, "", "", "Refurbishment of 24 rooms, final payment"],
        ["owner_distributions", "2027-10", -200_000, "", "", "Distribution planned after the season"],
        ["meetings_events", "2026-12", 58_000, "replace", "",
         "Christmas and year-end events: contracts signed, deposits invoiced"],
        ["rooms_direct", "2027-01", 14_000, "on top", "", "New airline crew contract, not in the historical run-rate"],
        ["rooms_direct", "2027-02", 14_000, "on top", "", "New airline crew contract, not in the historical run-rate"],
        ["rooms_direct", "2027-03", 14_000, "on top", "", "New airline crew contract, not in the historical run-rate"],
        ["other_receipts", "2027-02", 38_000, "on top", "2026-11",
         "Energy cost refund; only confirmed in November 2026, so not known at the forecast date"],
    ]
    write("known_events.csv", ["line_id", "month", "amount", "effect", "known_from", "note"], events)

    weak_season = [
        ["rooms_direct", -8, "", "", "", "Fewer direct bookings all year"],
        ["rooms_channels", -12, "", "", "", "Fewer bookings through online channels"],
        ["channel_commissions", -12, "", "", "", "Commissions fall with channel bookings"],
        ["food_beverage", -6, "", "", "", "Fewer guests in the restaurant and bar"],
        ["energy_utilities", 10, "", "2026-12", "", "Energy prices rise from December"],
        ["management_reserve", "", -7_000, "", "", "Contingency for unplanned repairs"],
    ]
    facility = [
        ["credit_line", "", 250_000, "2027-01", "2027-01", "Seasonal credit line drawn in January"],
        ["credit_line", "", -250_000, "2027-07", "2027-07", "Seasonal credit line repaid in July"],
        ["interest_paid", "", -1_300, "2027-02", "2027-07", "Interest on the drawn credit line"],
    ]
    write("scenarios.csv", ["scenario", "line_id", "percent", "amount", "from_month", "to_month", "note"],
          [["weak_season"] + row for row in weak_season]
          + [["weak_season_with_credit_line"] + row for row in weak_season + facility]
          + [
              ["more_direct_bookings", "rooms_direct", 10, "", "2027-03", "", "Guests book direct after the new website"],
              ["more_direct_bookings", "rooms_channels", -8, "", "2027-03", "", "The same guests no longer come through channels"],
              ["more_direct_bookings", "channel_commissions", -8, "", "2027-03", "", "Lower commissions on the bookings moved"],
              ["more_direct_bookings", "marketing_software", 12, "", "2027-01", "", "Website and campaign costs from January"],
              ["group_cancels", "meetings_events", "", -45_000, "2027-05", "2027-05",
               "Largest conference group cancels its May event"],
              ["refurbishment_overrun", "refurbishment", "", -60_000, "2027-03", "2027-03",
               "Refurbishment costs more and finishes a month late"],
          ])


if __name__ == "__main__":
    main()
