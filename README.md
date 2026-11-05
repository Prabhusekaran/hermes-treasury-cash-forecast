# Treasury Cash Forecast for Hermes Agent

A 12-month monthly cash-flow forecast for corporate treasury, as a Hermes plugin.

It uses the direct method: receipts and payments by type, from opening cash to
closing cash. You fill a template with your past cash-flow statement, your known
future payments and your what-if cases. Hermes checks it, runs the forecast, and
returns an Excel cash-flow statement with your last 12 actual months and the next
12 forecast months, tied to the cent.

**The language model never calculates a number.** It picks the tool and
explains the result. All figures come from a tested forecasting engine.

## What it does

Every cash-flow line gets one of three treatments:

| Treatment | Meaning | Example |
|---|---|---|
| `forecast` | A statistical model projects it from monthly history | Customer receipts, supplier payments |
| `known` | You supply the schedule; nothing is predicted | Payroll, tax instalments, loan repayments, approved capex |
| `assumption` | Applied only when you choose a scenario | Downside: domestic receipts 4% lower, a 20k monthly contingency |

Principle: **forecast what needs forecasting; supply what is already known.**

For each of the 12 months the engine enforces:

```
operating + investing + financing = net cash movement
opening cash + net cash movement  = closing cash
closing cash (month N)            = opening cash (month N+1)
```

If anything does not tie, the run stops and nothing is written.

## Tools

| Tool | What it does |
|---|---|
| `cash_forecast_template` | Writes the template (Excel or CSV), with a made-up example company or empty |
| `cash_forecast_validate` | Checks a filled template and lists every problem and warning in plain language |
| `cash_forecast_run` | Runs the forecast, reconciles it, saves the Excel statement and four audit files |
| `cash_forecast_compare` | Runs several models on the same inputs and shows how far the results differ |

A bundled skill, `treasury-cash-forecast:cash-forecast-mapping`, guides Hermes
through mapping your own spreadsheet into the template. The mapping is shown to
you for confirmation before anything runs.

## Try it

```
You:    Create the cash forecast example in ~/cash and run the base case and the downside.
Hermes: (cash_forecast_template -> cash_forecast_validate -> cash_forecast_run x2)
        Opening cash EUR 1,180,000.00, taken from your September 2026 closing balance.
        Base case: closing cash stays positive; the low point is EUR 1,015,554.06 in
        November 2026, the month of the equipment purchase.
        Downside (domestic receipts -4%, export receipts -8%, material prices +4%
        from January, a 20,000 monthly contingency): cash goes below zero in July
        and again in September 2027.
        One item was left out: the EUR 1,000,000 refinancing drawdown, because it
        was only agreed after the forecast date.
```

The example is a made-up manufacturer with 20 lines (`examples/synthetic_company`,
rebuilt by `examples/build_example.py`). Your own file can have as many lines as
you need.

## The template

Five tables: sheets in one `.xlsx`, or five `.csv` files in one folder.

| Table | Columns |
|---|---|
| `inputs` | `key`, `value`: `currency`, `origin_month` (last month of actuals), `data_kind` (`synthetic`, `actual`, `unspecified`) |
| `lines` | `line_id`, `label`, `section` (`operating` / `investing` / `financing`), `treatment` |
| `history` | Your past cash-flow statement: an `opening_cash` row, one row per line, a `closing_cash` row; one column per month (`2024-01`, `2024-02`, ...) |
| `known_events` | `line_id`, `month`, `amount`, optional `known_from`, `note` |
| `assumptions` | `scenario`, `line_id`, `percent` or `amount`, optional `from_month`, `to_month`, `note` |

Money in is positive, money out is negative. One currency per file.

**Opening and closing cash.** The history carries the opening and closing cash
of every month. The plugin checks that each month ties (opening cash + all lines
= closing cash, and closing cash = next opening cash) and warns where it does
not. The closing cash of `origin_month` becomes the opening cash of the forecast.

**Assumptions are percentages.** `percent` changes the size of a line: `-4` on
receipts means 4% lower receipts, `4` on supplier payments means 4% higher
payments. The percentage is taken from the line's own forecast for that month
(model forecast plus known schedule). Leave `from_month` and `to_month` empty
for all 12 months. Use `amount` only for a fixed monthly sum on an assumption
line, such as a contingency.

`known_from` is the month you learned about an item. It matters when you
forecast from a past month: an item you did not know about then is left out, so
a back-test cannot use hindsight.

## Models

| Model | Needs | Notes |
|---|---|---|
| `ets` (default) | 24 months | Holt-Winters, additive trend and seasonality |
| `sarima` | 36 months | Fixed (1,1,0)x(0,1,1,12) |
| `baseline`, `seasonal_drift` | 24 months | Simple, transparent |
| `seasonal_naive` | 12 months | Same month last year |
| `previous_month` | 1 month | Sanity check |
| `chronos_2` (optional) | 1 month | Amazon Chronos-2. Not installed by this plugin |

Chronos-2 runs only if you have installed `chronos-forecasting` yourself and
the `amazon/chronos-2` model is already on your machine. The plugin loads it
from local files and never downloads it.

## Outputs

`cash_forecast.xlsx` is the main result: a cash-flow statement with opening
cash, every line, section totals, net cash flow and closing cash, for your last
12 actual months and the 12 forecast months, plus a sheet showing where each
forecast figure comes from.

Four audit files sit beside it: `forecast.md` (readable report), `cash_bridge.csv` (monthly totals),
`lines.csv` (every line and month with its source), `forecast.json` (full audit
trail: inputs, model per line, items left out and why, file hashes).

By default they are saved in `cash_forecast_output/` next to your input.

## Limits

- Monthly, 12 months ahead, one currency, one entity.
- No uncertainty range for closing cash. Chronos gives a range per line only.
- The model comparison shows sensitivity to the model. It is not an accuracy test.
- A forecast from thin or unrepresentative history will be poor, whatever the model.
- `data_kind` is your declaration. The plugin cannot verify it.

## What the plugin touches

- **Network:** none.
- **Credentials:** none.
- **Files read:** only the workbook or folder you name.
- **Files written:** the template where you ask, and outputs next to your input or where you ask.
- **Shell commands, background processes, telemetry:** none.

## Where the engine comes from

`engine/` is a copy of the forecasting engine from
[Treasury-AI-Lab](https://github.com/Prabhusekaran/Treasury-AI-Lab), tag
`v0.1-generic-12m-cash-forecast`. That project validated it on synthetic data
only: a 17-fold rolling benchmark of eight models and ten stress scenarios. The
four small changes made to the copy are listed in `engine/__init__.py`.

No claim is made about accuracy on any real company.

## Development

```bash
pip install statsmodels openpyxl pytest
python -m pytest -q
hermes plugins validate . --install-deps
```

## Licence

MIT. Not financial advice.
