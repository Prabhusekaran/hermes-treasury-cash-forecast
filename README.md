# Treasury Cash Forecast for Hermes Agent

A 12-month monthly cash-flow forecast for corporate treasury, as a Hermes plugin.

You fill a template with your cash-flow lines. Hermes checks it, runs the
forecast, and returns a month-by-month cash bridge that ties to the cent.

**The language model never calculates a number.** It picks the tool and
explains the result. All figures come from a tested forecasting engine.

## What it does

Every cash-flow line gets one of three treatments:

| Treatment | Meaning | Example |
|---|---|---|
| `forecast` | A statistical model projects it from monthly history | Customer receipts, supplier payments |
| `known` | You supply the schedule; nothing is predicted | Payroll, tax instalments, loan repayments, approved capex |
| `assumption` | Applied only when you choose a scenario | Downside: collections 25k lower per month |

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
| `cash_forecast_run` | Runs the forecast, reconciles it, saves four output files |
| `cash_forecast_compare` | Runs several models on the same inputs and shows how far the results differ |

A bundled skill, `treasury-cash-forecast:cash-forecast-mapping`, guides Hermes
through mapping your own spreadsheet into the template. The mapping is shown to
you for confirmation before anything runs.

## Try it

```
You:    Create the cash forecast example in ~/cash and run the base case and the downside.
Hermes: (cash_forecast_template -> cash_forecast_validate -> cash_forecast_run x2)
        Base case: closing cash stays positive; the low point is EUR 528,433.59 in
        November 2026, the month of the equipment purchase.
        Downside: cash goes below zero in June and July 2027, when the tax
        instalment and the dividend fall together.
        One item was left out: the EUR 1,000,000 refinancing drawdown, because it
        was only agreed after the forecast date.
```

The example is a made-up company with 11 lines (`examples/synthetic_company`,
rebuilt by `examples/build_example.py`). Your own file can have as many lines as
you need.

## The template

Five tables: sheets in one `.xlsx`, or five `.csv` files in one folder.

| Table | Columns |
|---|---|
| `inputs` | `key`, `value`: `currency`, `origin_month` (last month of actuals), `opening_cash` (balance at the end of that month), `data_kind` (`synthetic`, `actual`, `unspecified`) |
| `lines` | `line_id`, `label`, `section` (`operating` / `investing` / `financing`), `treatment` |
| `history` | `line_id`, then one column per month (`2024-01`, `2024-02`, ...). Required for forecast lines; other lines are optional and kept for reference |
| `known_events` | `line_id`, `month`, `amount`, optional `known_from`, `note` |
| `assumptions` | `scenario`, `line_id`, `month`, `amount`, optional `available_from`, `note` |

Money in is positive, money out is negative. One currency per file.

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

`forecast.md` (readable report), `cash_bridge.csv` (monthly totals),
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
