"""Forecasting engine copied from Prabhusekaran/Treasury-AI-Lab.

Source: tag v0.1-generic-12m-cash-forecast (commit a0e85851d73c33b83ae0e42adf4b725a4bc037b5).
Changes made to the copy, and nothing else:
  1. `from treasury_lab.x import` became `from .x import` so the engine loads inside a plugin.
  2. `data_kind` also accepts "actual" (real company data declared by the user).
  3. The audit file list no longer names synthetic_cash_flow.py, which is not copied.
  4. One report line reworded ("Engineering example" removed).
  5. ETS starts from a heuristic level, trend and seasonal pattern instead of estimating
     them jointly with the smoothing weights. Found in the first live test: the lab's
     setting gave different forecasts on two machines for the same input. On the plugin's
     example a change of 1e-12 in the history moved single lines by up to 5% of their
     12-month total; with the heuristic start the whole statement moves by a few euros.
     On the lab's 17-fold rolling benchmark (seeds 42, 7, 101) the pooled error is about
     the same or slightly lower (WAPE 11.79 / 11.54 / 11.22 against 11.76 / 12.09 / 11.42).
     Because of this change the plugin's ETS figures are not the lab's ETS figures; the
     deterministic models (baseline and the seasonal ones) are unchanged and still match.
"""
