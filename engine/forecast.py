"""12-24 month monthly cash-flow forecast (first prototype).

The forecast has two clearly separate layers:

1. STATISTICAL BASELINE - learned from history only. It uses "underlying" cash
   flow, i.e. net cash flow with one-off items removed.
2. KNOWN EVENTS - business assumptions (a loan repayment, a new contract...)
   added on top, each with a certainty weight. They never influence the baseline.

Baseline method ("seasonal level + linear trend"):
- slope: the average change over the same month one year earlier, divided by 12;
- level: for each calendar month, the average of (value - slope * month number).
Forecast = level of that calendar month + slope * month number.

Run it with:  python -m treasury_lab.forecast
"""

from dataclasses import dataclass
from datetime import date

from .common import (
    EVENTS_FILE,
    HISTORY_FILE,
    OUTPUT_DIR,
    add_months,
    read_csv_rows,
    read_history,
    parse_month,
    write_csv,
)

MIN_HISTORY = 24     # at least two full years, so each month is seen twice
MAX_HORIZON = 24     # this module covers up to 24 months ahead

# How much of a known event's amount we include in the forecast.
CERTAINTY_WEIGHTS = {"confirmed": 1.0, "probable": 0.5}


# --- Layer 1: statistical baseline -------------------------------------------
@dataclass(frozen=True)
class BaselineModel:
    slope: float          # change in underlying cash flow per month
    month_level: dict     # calendar month (1-12) -> typical level
    last_month: date      # last month of history
    n_obs: int            # number of months of history used


def underlying_series(history):
    """Net cash flow with one-off items removed, one value per month."""
    return [row["net_cash_flow"] - row["one_off_items"] for row in history]


def check_monthly_sequence(history):
    """Raise ValueError if months are not consecutive."""
    for previous, current in zip(history, history[1:]):
        if current["month"] != add_months(previous["month"], 1):
            raise ValueError(
                f"History has a gap or is out of order after {previous['month']}"
            )


def fit_baseline(history):
    """Learn the baseline from a list of monthly history rows."""
    if len(history) < MIN_HISTORY:
        raise ValueError(f"Need at least {MIN_HISTORY} months of history")
    check_monthly_sequence(history)

    values = underlying_series(history)

    yearly_changes = [values[i] - values[i - 12] for i in range(12, len(values))]
    slope = sum(yearly_changes) / len(yearly_changes) / 12

    detrended = {month: [] for month in range(1, 13)}
    for i, (row, value) in enumerate(zip(history, values)):
        detrended[row["month"].month].append(value - slope * i)
    month_level = {m: sum(v) / len(v) for m, v in detrended.items()}

    return BaselineModel(
        slope=slope,
        month_level=month_level,
        last_month=history[-1]["month"],
        n_obs=len(history),
    )


def statistical_forecast(model, horizon):
    """Baseline forecast for the next `horizon` months (1 to 24)."""
    if not 1 <= horizon <= MAX_HORIZON:
        raise ValueError(f"horizon must be between 1 and {MAX_HORIZON}")

    rows = []
    for step in range(1, horizon + 1):
        month = add_months(model.last_month, step)
        index = model.n_obs - 1 + step
        value = model.month_level[month.month] + model.slope * index
        rows.append({"month": month, "baseline_forecast": value})
    return rows


def seasonal_naive_forecast(history, horizon):
    """Benchmark: repeat the underlying values of the last 12 months."""
    last_year = underlying_series(history)[-12:]
    return [last_year[step % 12] for step in range(horizon)]


# --- Layer 2: known events (business assumptions) -----------------------------
def read_known_events(path=EVENTS_FILE):
    """Read known events: months become dates, the amount a float."""
    events = []
    for raw in read_csv_rows(path):
        event = dict(raw)
        event["start_month"] = parse_month(raw["start_month"])
        event["end_month"] = parse_month(raw["end_month"])
        event["monthly_amount"] = float(raw["monthly_amount"])
        events.append(event)
    return events


def event_adjustments(events, months, weights=CERTAINTY_WEIGHTS):
    """Weighted event amounts per month, as {month: amount}."""
    adjustments = {month: 0.0 for month in months}
    for event in events:
        if event["certainty"] not in weights:
            raise ValueError(f"Unknown certainty level: {event['certainty']!r}")
        weight = weights[event["certainty"]]
        for month in months:
            if event["start_month"] <= month <= event["end_month"]:
                adjustments[month] += event["monthly_amount"] * weight
    return adjustments


# --- Putting the two layers together -----------------------------------------
def combine_forecast(baseline_rows, adjustments):
    """Add the event layer to the baseline; keep all three columns visible."""
    rows = []
    for row in baseline_rows:
        adjustment = adjustments.get(row["month"], 0.0)
        rows.append({
            "month": row["month"],
            "baseline_forecast": row["baseline_forecast"],
            "known_events_adjustment": adjustment,
            "total_forecast": row["baseline_forecast"] + adjustment,
        })
    return rows


def forecast_cash_flow(history, events, horizon=12):
    """Full forecast: baseline from history plus known events."""
    model = fit_baseline(history)
    baseline_rows = statistical_forecast(model, horizon)
    adjustments = event_adjustments(events, [r["month"] for r in baseline_rows])
    return combine_forecast(baseline_rows, adjustments)


def main():
    if not HISTORY_FILE.exists() or not EVENTS_FILE.exists():
        print("Synthetic data not found. Run first: python -m treasury_lab.generate_data")
        return 1

    rows = forecast_cash_flow(read_history(), read_known_events(), horizon=24)
    output_file = OUTPUT_DIR / "forecast_12_24m.csv"
    write_csv(output_file, rows, list(rows[0].keys()))

    print(f"{'month':<12}{'baseline':>14}{'events':>14}{'total':>14}")
    for row in rows:
        print(f"{row['month']!s:<12}{row['baseline_forecast']:>14,.0f}"
              f"{row['known_events_adjustment']:>14,.0f}{row['total_forecast']:>14,.0f}")
    print(f"\nWrote {output_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
