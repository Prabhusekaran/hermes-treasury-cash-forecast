"""Tool handlers. Each takes the model's arguments and returns a JSON string.

The language model never calculates a number here. It picks a tool; the engine
does the arithmetic and the reconciliation, and the handler reports the result.
Handlers never raise: every failure comes back as {"success": false, ...}.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

from .engine.cash_flow_orchestration import write_outputs
from .engine.cash_flow_schema import HORIZON
from .engine.evaluate import MODEL_REGISTRY
from .loader import TemplateError, load_inputs
from .scenarios import BASE, run_all
from .statement_xlsx import write_workbook
from .template import write_template

DEFAULT_MODEL = "ets"
# TiRex-2 is in the engine but not offered: it needs a compiler toolchain most users lack.
MODELS = {
    "ets": "Holt-Winters exponential smoothing (trend + yearly seasonality). Default.",
    "sarima": "Seasonal ARIMA with a fixed specification.",
    "baseline": "Seasonal level plus linear trend.",
    "seasonal_drift": "Same month last year plus the average yearly change.",
    "seasonal_naive": "Same month last year.",
    "previous_month": "Last month repeated. A sanity check, not a forecast.",
    "chronos_2": "Amazon Chronos-2 foundation model. Optional: needs the chronos-forecasting "
                 "package and the model already downloaded on this machine.",
}
SIMPLE_MODELS = ("ets", "sarima", "baseline", "seasonal_drift", "seasonal_naive", "previous_month")


def _reply(payload):
    return json.dumps(payload, indent=2, default=str)


def _fail(message, problems=None, **extra):
    body = {"success": False, "error": message}
    if problems:
        body["problems"] = problems
    body.update(extra)
    return _reply(body)


def _min_history(model):
    return MODEL_REGISTRY[model].min_history


def _model_readiness(inputs):
    """For each model: can it run on this history, and if not, why not."""
    months = inputs.history_months()
    forecast_lines = [l.line_id for l in inputs.schema.lines if l.treatment == "forecast"]
    shortest = min((months.get(line, 0) for line in forecast_lines), default=0)
    ready = {}
    for name in MODELS:
        need = _min_history(name)
        ready[name] = {"can_run": shortest >= need or not forecast_lines, "needs_months": need}
    return ready, shortest


def _overview(inputs):
    lines = inputs.schema.lines
    months = inputs.history_months()
    return {
        "source": inputs.source,
        "currency": inputs.schema.currency,
        "data_kind": inputs.data_kind,
        "origin_month": inputs.origin_month.isoformat(),
        "opening_cash": f"{inputs.opening_cash.amount:.2f}",
        "opening_cash_taken_from": inputs.opening_cash_source,
        "history_ties_opening_to_closing": {True: "yes", False: "no, see warnings",
                                            None: "not checked"}[inputs.history_ties],
        "lines": [{"line_id": l.line_id, "label": l.label, "section": l.section,
                   "treatment": l.treatment,
                   **({"history_months": months.get(l.line_id, 0)} if l.treatment == "forecast" else {})}
                  for l in lines],
        "known_amounts": len(inputs.known_events),
        "model_figure_replaced_by_known_amount": len(inputs.replace_ids),
        "scenarios": inputs.scenarios,
    }


def _check_request(inputs, model, scenarios):
    problems = []
    if model not in MODELS:
        problems.append(f"Unknown model '{model}'. Choose one of: {', '.join(MODELS)}.")
    else:
        ready, shortest = _model_readiness(inputs)
        if not ready[model]["can_run"]:
            usable = [m for m in SIMPLE_MODELS if ready[m]["can_run"]]
            problems.append(f"Model '{model}' needs {ready[model]['needs_months']} months of history; "
                            f"the shortest forecast line has {shortest}. "
                            + (f"Models that can run: {', '.join(usable)}." if usable else
                               "Add more history."))
    for scenario in scenarios:
        if scenario not in inputs.scenarios:
            problems.append(f"Scenario '{scenario}' is not in the scenarios table. "
                            f"Available: {', '.join(inputs.scenarios) or 'none'}.")
    return problems


def _wanted_scenarios(args, inputs):
    """Scenarios to run next to the base case: the ones asked for, otherwise all of them."""
    asked = args.get("scenarios")
    if asked is None and args.get("scenario"):
        asked = [args["scenario"]]
    if asked is None:
        return list(inputs.scenarios)
    if isinstance(asked, str):
        asked = [part for part in asked.split(",")]
    names = [str(name).strip() for name in asked if str(name).strip()]
    return [name for name in dict.fromkeys(names) if name.lower() not in (BASE, "base case", "none")]


def _run(inputs, model, scenarios):
    """Return (runs, error). Model warnings are not hidden: they are attached as runs['_notes']."""
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            runs = run_all(inputs, model, scenarios)
        notes = list(dict.fromkeys(f"{w.category.__name__}: {str(w.message).strip()}" for w in caught))
        return {**runs, "_notes": notes}, None
    except (ImportError, OSError) as exc:
        if model == "chronos_2":
            return None, ("Chronos-2 is not available on this machine. Install the optional package "
                          "'chronos-forecasting' and download 'amazon/chronos-2' once, or use model 'ets'. "
                          f"({type(exc).__name__})")
        return None, f"Model '{model}' could not be loaded: {exc}"
    except Exception as exc:   # engine validation, model failure: report, never guess
        return None, f"The forecast stopped: {exc}"


def _scenario_effect(run):
    """Total 12-month effect of the scenario on each line it touches."""
    totals = {}
    for month in run.statement.months:
        for line in month.lines:
            if line.assumption_overlay:
                totals[line.line_id] = totals.get(line.line_id, 0) + line.assumption_overlay
    effect = {line_id: str(amount) for line_id, amount in totals.items()}
    if effect:
        effect["total_effect_on_final_closing_cash"] = str(sum(totals.values()))
    return effect


def _summary(run):
    months = run.statement.months
    low = min(months, key=lambda m: m.closing_cash)
    return {
        "final_closing_cash": str(months[-1].closing_cash),
        "lowest_closing_cash": {"month": low.month.isoformat(), "amount": str(low.closing_cash)},
        "months_below_zero": [m.month.isoformat() for m in months if m.closing_cash < 0],
    }


def _bridge(run):
    return [{"month": m.month.isoformat(),
             "operating": str(m.sections["operating"]), "investing": str(m.sections["investing"]),
             "financing": str(m.sections["financing"]), "net_cash_flow": str(m.net_cash_movement),
             "opening": str(m.opening_cash), "closing": str(m.closing_cash)}
            for m in run.statement.months]


def _folder_name(text):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in text) or "scenario"


# --- handlers -----------------------------------------------------------------
def cash_forecast_template(args: dict, **kwargs) -> str:
    try:
        output_dir = (args.get("output_dir") or "").strip()
        if not output_dir:
            return _fail("output_dir is required: the folder where the template should be saved.")
        path = write_template(output_dir, (args.get("format") or "xlsx").lower(),
                              bool(args.get("with_example", True)))
        return _reply({
            "success": True, "path": str(path),
            "contains": "synthetic example company (made-up data)" if args.get("with_example", True)
                        else "empty template",
            "next_step": "Fill the five tables, then call cash_forecast_validate with this path.",
            "tables": ["inputs", "lines", "history", "known_events", "scenarios"],
            "layout": "history is a direct cash-flow statement: opening_cash row, one row per line, "
                      "closing_cash row. Every line_id in 'lines' needs a row in 'history'; known lines "
                      "need their future amounts in 'known_events'. scenarios are percentages (or fixed "
                      "amounts) per scenario name.",
        })
    except TemplateError as exc:
        return _fail("The template could not be written.", exc.problems)
    except Exception as exc:
        return _fail(f"The template could not be written: {exc}")


def cash_forecast_validate(args: dict, **kwargs) -> str:
    try:
        path = (args.get("input_path") or "").strip()
        if not path:
            return _fail("input_path is required: the .xlsx workbook or the folder of CSV files.")
        inputs = load_inputs(path)
        ready, shortest = _model_readiness(inputs)
        return _reply({
            "success": True, "ready_to_run": True, **_overview(inputs),
            "shortest_forecast_history_months": shortest,
            "models": {name: {**ready[name], "about": MODELS[name]} for name in MODELS},
            "warnings": inputs.warnings,
            "next_step": "Call cash_forecast_run. Show the user the warnings first.",
        })
    except TemplateError as exc:
        return _fail("The input has problems. Nothing was forecast.", exc.problems, ready_to_run=False)
    except Exception as exc:
        return _fail(f"The input could not be read: {exc}", ready_to_run=False)


def cash_forecast_run(args: dict, **kwargs) -> str:
    try:
        path = (args.get("input_path") or "").strip()
        if not path:
            return _fail("input_path is required: the .xlsx workbook or the folder of CSV files.")
        model = (args.get("model") or DEFAULT_MODEL).strip().lower()
        inputs = load_inputs(path)
        scenarios = _wanted_scenarios(args, inputs)
        problems = _check_request(inputs, model, scenarios)
        if problems:
            return _fail("The forecast was not run.", problems)
        runs, error = _run(inputs, model, scenarios)
        if error:
            return _fail(error)
        model_notes = runs.pop("_notes")
        source = Path(inputs.source)
        default_dir = (source if source.is_dir() else source.parent) / "cash_forecast_output" / \
            f"{inputs.origin_month:%Y-%m}_{model}"
        output_dir = Path(str(args.get("output_dir") or default_dir)).expanduser()
        audit_files = []
        for name, run in runs.items():
            audit_files += [str(f) for f in write_outputs(run, output_dir / "audit" / _folder_name(name))]
        files = []
        try:
            files.append(str(write_workbook(output_dir / "cash_forecast.xlsx", runs, inputs)))
        except ImportError:
            inputs.warnings.append("The Excel statement was not written because 'openpyxl' is not installed.")
        base = runs[BASE]
        months = [m.month.isoformat() for m in base.statement.months]
        return _reply({
            "success": True,
            "model": model, "data_kind": inputs.data_kind, "currency": inputs.schema.currency,
            "origin_month": inputs.origin_month.isoformat(), "horizon_months": HORIZON,
            "opening_cash": str(base.statement.opening_cash),
            "opening_cash_taken_from": inputs.opening_cash_source,
            "reconciled": True,
            "reconciliation": "Every month, in every scenario: operating + investing + financing = net cash "
                              "flow; opening + net cash flow = closing; closing = next opening. Checked to the cent.",
            "scenarios": {name: {**_summary(run),
                                 **({"effect_by_line": _scenario_effect(run)} if name != BASE else {})}
                          for name, run in runs.items()},
            "closing_cash_by_month": [{"month": month, **{name: str(run.statement.months[i].closing_cash)
                                                         for name, run in runs.items()}}
                                      for i, month in enumerate(months)],
            "base_cash_bridge": _bridge(base),
            "model_runs": list(base.model_audit),
            "model_notes": model_notes,
            "model_figure_replaced_by_known_amount": base.inputs["model_forecast_replaced_by_known_amount"],
            "left_out_inputs": list({d.input_id: {"kind": d.kind, "id": d.input_id, "line_id": d.line_id,
                                                  "reason": d.reason}
                                     for run in runs.values() for d in run.statement.excluded_inputs
                                     if d.reason != "scenario_not_selected"}.values()),
            "scenarios_not_run": [s for s in inputs.scenarios if s not in runs],
            "warnings": inputs.warnings,
            "limits": ["Uncertainty ranges exist per line only (Chronos); there is no range for closing cash.",
                       "The forecast is only as good as the history and schedules supplied."],
            "main_file": files[0] if files else None,
            "audit_files": audit_files,
        })
    except TemplateError as exc:
        return _fail("The input has problems. Nothing was forecast.", exc.problems)
    except Exception as exc:
        return _fail(f"The forecast stopped: {exc}")


def cash_forecast_compare(args: dict, **kwargs) -> str:
    """Run several models on the same inputs so no single model is trusted blindly."""
    try:
        path = (args.get("input_path") or "").strip()
        if not path:
            return _fail("input_path is required: the .xlsx workbook or the folder of CSV files.")
        scenario = (args.get("scenario") or "").strip()
        if scenario.lower() in (BASE, "base case", "none"):
            scenario = ""
        requested = args.get("models") or list(SIMPLE_MODELS)
        if isinstance(requested, str):
            requested = [m.strip() for m in requested.split(",") if m.strip()]
        requested = [str(m).strip().lower() for m in requested]
        inputs = load_inputs(path)
        if scenario and scenario not in inputs.scenarios:
            return _fail("The comparison was not run.", _check_request(inputs, DEFAULT_MODEL, [scenario]))
        results, skipped = [], []
        for model in dict.fromkeys(requested):
            problems = _check_request(inputs, model, [])
            if problems:
                skipped.append({"model": model, "reason": problems[0]})
                continue
            runs, error = _run(inputs, model, [scenario] if scenario else [])
            if error:
                skipped.append({"model": model, "reason": error})
                continue
            notes = runs.pop("_notes")
            results.append({"model": model, **_summary(runs[scenario or BASE]),
                            **({"model_notes": notes} if notes else {})})
        if not results:
            return _fail("No model could run.", [s["reason"] for s in skipped])
        closings = [float(r["final_closing_cash"]) for r in results]
        return _reply({
            "success": True, "scenario": scenario or BASE,
            "origin_month": inputs.origin_month.isoformat(), "currency": inputs.schema.currency,
            "results": results, "skipped": skipped,
            "spread_of_final_closing_cash": f"{max(closings) - min(closings):.2f}",
            "how_to_read": "Known schedules and scenarios are identical in every run; only the forecast "
                           "lines differ. A wide spread means the forecast lines are hard to predict and "
                           "the result depends on the model. This is a comparison, not an accuracy test.",
            "warnings": inputs.warnings,
        })
    except TemplateError as exc:
        return _fail("The input has problems. Nothing was forecast.", exc.problems)
    except Exception as exc:
        return _fail(f"The comparison stopped: {exc}")
