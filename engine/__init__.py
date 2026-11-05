"""Forecasting engine copied from Prabhusekaran/Treasury-AI-Lab.

Source: tag v0.1-generic-12m-cash-forecast (commit a0e85851d73c33b83ae0e42adf4b725a4bc037b5).
Changes made to the copy, and nothing else:
  1. `from treasury_lab.x import` became `from .x import` so the engine loads inside a plugin.
  2. `data_kind` also accepts "actual" (real company data declared by the user).
  3. The audit file list no longer names synthetic_cash_flow.py, which is not copied.
  4. One report line reworded ("Engineering example" removed).
"""
