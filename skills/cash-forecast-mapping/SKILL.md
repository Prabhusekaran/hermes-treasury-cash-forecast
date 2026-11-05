---
name: cash-forecast-mapping
description: Map a company's own cash-flow spreadsheet into the Treasury Cash Forecast template, confirm the mapping with the user, then validate and run.
---

# Mapping a cash-flow spreadsheet into the forecast template

Use this when the user has their own cash-flow file (Excel or CSV) and wants a
12-month cash forecast from it. Your job is the mapping. The numbers are the
engine's job: never calculate, estimate or adjust a cash figure yourself.

## Steps

1. Call `cash_forecast_template` with `with_example: true` so you and the user
   can see the target format. Read the user's file.
2. Propose a mapping table and show it to the user before writing anything:

   | Their row | line_id | section | treatment | Why |
   |---|---|---|---|---|

3. Wait for the user to confirm or correct the mapping. Do not continue on your
   own judgement; a wrong treatment changes the forecast.
4. Write the filled template (five tables) from their data, copying amounts
   exactly as they appear.
5. Call `cash_forecast_validate`. Show every problem and warning to the user.
6. When validation passes and the user agrees, call `cash_forecast_run`.
7. Report the returned figures exactly. Name the lowest-cash month and any
   month below zero. Say which lines came from a model and which from the
   schedule. Point the user to `cash_forecast.xlsx`, the cash-flow statement.

## How to choose section and treatment

- **section**: operating (day-to-day business), investing (capex, acquisitions,
  disposals), financing (debt, equity, dividends, interest on debt if the user
  books it there). At least one line per section is required; if the user has
  none for a section, add one line and leave it empty.
- **forecast**: recurring flows with at least 24 months of monthly history and
  no fixed schedule, such as customer receipts or supplier payments.
- **known**: flows fixed by contract or already decided, such as payroll, tax
  instalments, loan repayments, approved capex. The user supplies the amounts.
- **assumption**: what-if items that should only count in a named scenario.

## Rules that protect the result

- Skip subtotal and formula rows (EBITDA, "total operating cash flow", cash
  period end). The engine derives every total. Mapping a subtotal as a line
  counts it twice.
- Skip non-cash rows (depreciation, provisions, accruals) and balance-sheet
  balances. Only cash movements belong in the template.
- Money in is positive, money out is negative. If the user's file shows
  payments as positive numbers, ask before flipping the sign.
- History for a forecast line must be the recurring part only. If a known
  event sits on a forecast line, it is added on top of the model forecast, so
  the same item must not also be inside that line's history.
- If the file is an indirect cash-flow statement (it starts from profit or
  EBITDA and adjusts for working capital), say so. This template is a direct
  statement of receipts and payments, and a line-by-line mapping from an
  indirect statement needs the user's decisions, not yours.
- One currency per template. Do not convert currencies.
- The history sheet is the user's past cash-flow statement: an `opening_cash`
  row, one row per line, a `closing_cash` row. Copy their balances; never
  calculate a balance yourself. If validation says the history does not tie,
  show the user the months and amounts and ask what is missing (often FX effect
  on cash or an unmapped row). Do not plug the difference.
- Scenario assumptions are percentages of a line (`-5` = 5% lower receipts,
  `5` on a payment line = 5% higher payments). Ask the user for the
  percentages; do not invent them. Use `amount` only on an assumption line.
- Set `data_kind` to what the user tells you (synthetic, actual, unspecified).

## What to say about limits

- ETS needs 24 months of history; SARIMA needs 36.
- Uncertainty ranges exist per line only (Chronos); there is no range for
  closing cash.
- `cash_forecast_compare` shows how much the result depends on the model. It is
  not an accuracy test.
