"""Tool handlers. Each takes the model's arguments and returns a JSON string.

The language model never calculates a number here. It picks a tool; the engine
does the arithmetic and the reconciliation, and the handler reports the result.
Handlers never raise: every failure comes back as {"success": false, ...}.
"""
from __future__ import annotations

import importlib.util
import json
import warnings
from pathlib import Path

from .engine.cash_flow_orchestration import write_outputs
from .engine.cash_flow_schema import HORIZON
from .engine.common import add_months
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


LIBRARIES = {"statsmodels": "the ETS and SARIMA models", "openpyxl": "reading and writing Excel files"}
RESTART_HINT = ("The plugin's libraries are installed together with the plugin, but a Hermes process that was "
                "already running only sees them after a restart. Ask the user to restart Hermes "
                "('hermes gateway restart', or close and reopen the Hermes CLI) and try again. Do not "
                "'pip install' anything, and do not switch to another model or file format on your own: "
                "that changes the figures and the output the user asked for.")


def _missing_libraries():
    missing = []
    for name in LIBRARIES:
        try:
            found = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(name)
    return missing


def _library_problem(missing):
    needs = "; ".join(f"'{name}' ({LIBRARIES[name]})" for name in missing)
    return f"Hermes cannot load {needs} in this session. {RESTART_HINT}"


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
    months = inputs.history_months()
    horizon = {add_months(inputs.origin_month, i) for i in range(1, HORIZON + 1)}
    lines = []
    for line in inputs.schema.lines:
        known = [e for e in inputs.known_events if e.line_id == line.line_id
                 and e.effective_month in horizon and e.known_from_month <= inputs.origin_month]
        scenarios = sorted({r.scenario for r in inputs.rules if r.line_id == line.line_id}
                           | {a.scenario_id for a in inputs.assumptions if a.line_id == line.line_id})
        entry = {"line_id": line.line_id, "label": line.label, "section": line.section,
                 "treatment": line.treatment}
        if line.treatment == "forecast":
            entry["history_months"] = months.get(line.line_id, 0)
        if known:
            entry["known_amounts"] = len(known)
            entry["known_total"] = f"{sum(float(e.amount) for e in known):.2f}"
            replacing = sum(e.event_id in inputs.replace_ids for e in known)
            if line.treatment == "forecast":
                entry["known_amounts_replacing_the_model"] = replacing
                entry["known_amounts_on_top_of_the_model"] = len(known) - replacing
        if scenarios:
            entry["scenarios"] = scenarios
        lines.append(entry)
    return {
        "source": inputs.source,
        "currency": inputs.schema.currency,
        "data_kind": inputs.data_kind,
        "origin_month": inputs.origin_month.isoformat(),
        "opening_cash": f"{inputs.opening_cash.amount:.2f}",
        "opening_cash_taken_from": inputs.opening_cash_source,
        "history_ties_opening_to_closing": {True: "yes", False: "no, see warnings",
                                            None: "not checked"}[inputs.history_ties],
        "model_chosen_in_inputs": inputs.model,
        "minimum_cash": None if inputs.minimum_cash is None else f"{inputs.minimum_cash:.2f}",
        "lines": lines,
        "known_amounts_in_the_next_12_months": sum(l.get("known_amounts", 0) for l in lines),
        "scenarios": inputs.scenarios,
        "check_with_the_user": "Show the user the known amounts and scenarios found per line and ask "
                               "whether all of them still apply. Rows left over from the example or from "
                               "an earlier forecast must be deleted before running.",
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
        missing = _missing_libraries()
        if missing:
            return None, _library_problem(missing)
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


def _summary(run, minimum_cash=None):
    months = run.statement.months
    low = min(months, key=lambda m: m.closing_cash)
    summary = {
        "final_closing_cash": str(months[-1].closing_cash),
        "lowest_closing_cash": {"month": low.month.isoformat(), "amount": str(low.closing_cash)},
        "months_below_zero": [m.month.isoformat() for m in months if m.closing_cash < 0],
    }
    if minimum_cash is not None:
        summary["months_below_minimum_cash"] = [m.month.isoformat() for m in months
                                                if float(m.closing_cash) < minimum_cash]
    return summary


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
        file_format = (args.get("format") or "xlsx").lower()
        if file_format == "xlsx" and "openpyxl" in _missing_libraries():
            return _fail(_library_problem(["openpyxl"]))
        path = write_template(output_dir, file_format, bool(args.get("with_example", True)))
        return _reply({
            "success": True, "path": str(path),
            "contains": "synthetic example company (made-up data)" if args.get("with_example", True)
                        else "empty template",
            "next_step": "Fill the four tables, then call cash_forecast_validate with this path.",
            "tables": ["inputs", "history", "known_events", "scenarios"],
            "layout": "history is a direct cash-flow statement and defines the lines: opening_cash row, "
                      "one row per line (line_id, label, section, treatment, then the months), "
                      "closing_cash row. Known lines need their future amounts in 'known_events'. "
                      "scenarios are percentages (or fixed amounts) per scenario name. The example rows in "
                      "known_events and scenarios must be deleted before the user enters their own.",
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
        if inputs.model and inputs.model not in MODELS:
            return _fail("The input has problems. Nothing was forecast.",
                         [f"inputs: model '{inputs.model}' is not available. Choose one of: {', '.join(MODELS)}, "
                          "or leave it empty for ets."], ready_to_run=False)
        ready, shortest = _model_readiness(inputs)
        missing = _missing_libraries()
        if missing:
            inputs.warnings.insert(0, _library_problem(missing))
        return _reply({
            "success": True, "ready_to_run": not missing,
            "libraries_missing_in_this_session": missing, **_overview(inputs),
            "shortest_forecast_history_months": shortest,
            "models": {name: {**ready[name], "about": MODELS[name]} for name in MODELS},
            "warnings": inputs.warnings,
            "next_step": ("Stop and tell the user to restart Hermes (see the first warning)." if missing else
                          "Call cash_forecast_run. Show the user the warnings first."),
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
        inputs = load_inputs(path)
        if "openpyxl" in _missing_libraries():
            return _fail(_library_problem(["openpyxl"]))       # the Excel result could not be written
        model = (args.get("model") or inputs.model or DEFAULT_MODEL).strip().lower()
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
            "minimum_cash": None if inputs.minimum_cash is None else f"{inputs.minimum_cash:.2f}",
            "scenarios": {name: {**_summary(run, inputs.minimum_cash),
                                 **({"effect_by_line": _scenario_effect(run)} if name != BASE else {})}
                          for name, run in runs.items()},
            "closing_cash_by_month": [{"month": month, **{name: str(run.statement.months[i].closing_cash)
                                                         for name, run in runs.items()}}
                                      for i, month in enumerate(months)],
            "base_cash_bridge": _bridge(base),
            "model_runs": list(base.model_audit),
            "model_notes": model_notes,
            "model_figure_replaced_by_known_amount": base.inputs["model_forecast_replaced_by_known_amount"],
            "model_continued_from_known_amounts": base.inputs["model_continued_from_known_amounts"],
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
