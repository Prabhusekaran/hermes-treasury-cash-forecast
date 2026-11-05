"""Generic 12-month cash-flow orchestration and auditable, cent-exact exports."""
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import csv
import hashlib
import importlib.metadata
import json
import sys
from pathlib import Path
from typing import Mapping

from .cash_flow import CashFlowStatement, assemble_cash_flow, validate_cash_flow
from .cash_flow_forecast import forecast_line
from .cash_flow_schema import (
    CashFlowSchema, ForecastRequest, HistoricalLineValue, HORIZON, OpeningCash,
    identifier, month,
)


@dataclass(frozen=True)
class ForecastRun:
    schema: CashFlowSchema
    statement: CashFlowStatement
    inputs: Mapping
    model_audit: tuple[dict, ...]
    history_excluded_after_origin_n: int
    execution_kind: str
    data_kind: str


def run_cash_flow(
    schema, history, origin_month, opening_cash, *, known_events=(), assumptions=(),
    scenario_id=None, model_name="chronos_2", line_models=None,
    fallback_model=None, model_specs=None, data_kind="unspecified",
):
    """Route only forecast lines; assemble all treatments using existing rules.

    Fallback is explicit and limited to dependency/cache availability failures.
    Invalid history, invalid outputs and model runtime errors fail loudly.
    Registry injection is for tests; those runs are labeled injected_model_specs.
    """
    if not isinstance(schema, CashFlowSchema) or not isinstance(opening_cash, OpeningCash):
        raise ValueError("schema and opening cash contracts required")
    schema.__post_init__()
    if data_kind not in ("synthetic", "actual", "unspecified"):
        raise ValueError("data_kind must be synthetic, actual or unspecified")
    month(origin_month, "origin_month")
    opening_cash.__post_init__()
    if opening_cash.as_of_month != origin_month:
        raise ValueError("opening cash must match forecast origin")
    history, known_events, assumptions = map(tuple, (history, known_events, assumptions))
    lines = {line.line_id: line for line in schema.lines}
    for row in history:
        if not isinstance(row, HistoricalLineValue):
            raise ValueError("history must use HistoricalLineValue contracts")
        row.__post_init__()
        if row.line_id not in lines:
            raise ValueError("history references unknown line")
    keys = [(row.line_id, row.month) for row in history]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate history line/month")
    identifier(model_name, "model_name")
    overrides = dict(line_models or {})
    forecast_ids = {line.line_id for line in schema.lines if line.treatment == "forecast"}
    if set(overrides) - forecast_ids:
        raise ValueError("model overrides must reference forecast lines")
    for name in overrides.values():
        identifier(name, "line model")
    if fallback_model is not None:
        identifier(fallback_model, "fallback_model")
    injected = model_specs is not None
    if model_specs is None:
        from .evaluate import MODEL_REGISTRY
        model_specs = MODEL_REGISTRY
    requested = {overrides.get(line_id, model_name) for line_id in forecast_ids}
    if requested - set(model_specs) or (fallback_model is not None and fallback_model not in model_specs):
        raise ValueError("unknown requested/fallback model")
    grouped = {line_id: [] for line_id in lines}
    for row in history:
        if row.month <= origin_month:
            grouped[row.line_id].append(row)
    forecasts, audit = [], []
    for line in schema.lines:
        if line.treatment != "forecast":
            continue
        primary = overrides.get(line.line_id, model_name)
        request = ForecastRequest(line.line_id, origin_month, primary,
                    tuple(sorted(grouped[line.line_id], key=lambda r: r.month)))
        used, reason = primary, None
        try:
            result = forecast_line(schema, request, model_specs=model_specs)
        except (ImportError, OSError) as exc:
            if fallback_model is None or fallback_model == primary:
                raise
            from dataclasses import replace
            used, reason = fallback_model, type(exc).__name__
            result = forecast_line(schema, replace(request, model_name=used), model_specs=model_specs)
        forecasts.append(result)
        audit.append({"line_id": line.line_id, "requested_model": primary,
                      "used_model": used, "fallback_reason": reason,
                      "train_months": len(request.history),
                      "first_train_month": request.history[0].month,
                      "last_train_month": request.history[-1].month})
    statement = assemble_cash_flow(schema, origin_month, opening_cash,
        forecasts=forecasts, known_events=known_events, assumptions=assumptions,
        scenario_id=scenario_id)
    inputs = {"schema": schema, "history": history, "origin_month": origin_month,
              "opening_cash": opening_cash, "known_events": known_events,
              "assumptions": assumptions, "scenario_id": scenario_id,
              "requested_model": model_name, "line_models": overrides,
              "fallback_model": fallback_model, "base_forecasts": tuple(forecasts)}
    return ForecastRun(schema, statement, inputs, tuple(audit),
        sum(row.month > origin_month for row in history),
        "injected_model_specs" if injected else "existing_registry", data_kind)


def _json_value(value):
    """Decimal cash amounts remain strings, so JSON never loses cents."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(v) for v in value]
    return value


def _source(line):
    components = []
    if line.model_name is not None:
        components.append("model_forecast")
    if line.event_ids:
        components.append("supplied_known")
    if line.assumption_ids:
        components.append("management_assumption")
    return "+".join(components) or "zero_unscheduled"


def report_document(run, *, source_commit=None):
    """Include all supplied/excluded inputs and unrounded base model forecasts."""
    validate_cash_flow(run.statement, run.schema)
    schema = {line.line_id: line for line in run.schema.lines}
    applied_events = sorted({e for row in run.statement.months for line in row.lines for e in line.event_ids})
    applied_assumptions = sorted({a for row in run.statement.months for line in row.lines for a in line.assumption_ids})
    versions = {}
    for package in ("statsmodels", "chronos-forecasting", "torch", "tirex-2"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    document = _json_value(run.statement)
    for row, source_row in zip(document["months"], run.statement.months):
        for line, source_line in zip(row["lines"], source_row.lines):
            definition = schema[source_line.line_id]
            line.update({"label": definition.label, "section": definition.section,
                         "treatment": definition.treatment, "source": _source(source_line)})
    input_document = _json_value(run.inputs)
    digest = hashlib.sha256(json.dumps(input_document, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    document["audit"] = _json_value({
        "forecast_origin": run.statement.origin_month, "horizon": HORIZON,
        "requested_model": run.inputs["requested_model"],
        "used_models": sorted({r["used_model"] for r in run.model_audit}),
        "model_runs": run.model_audit, "execution_kind": run.execution_kind,
        "selected_scenario": run.statement.scenario_id,
        "applied_known_event_ids": applied_events,
        "applied_assumption_ids": applied_assumptions,
        "history_excluded_after_origin_n": run.history_excluded_after_origin_n,
        "schema_version": run.schema.schema_version, "source_commit": source_commit,
        "completed_utc": datetime.now(timezone.utc),
        "python_version": sys.version.split()[0],
        "package_versions": versions, "input_and_base_forecast_sha256": digest,
        "synthetic_only": run.data_kind == "synthetic",
        "data_kind": run.data_kind, "cash_encoding": "decimal strings in currency units",
        "quantile_contract": "conditional line q10/q50/q90 only; no joint cash intervals",
    })
    document["inputs"] = input_document
    source_files = ("cash_flow_orchestration.py",
                    "cash_flow.py", "cash_flow_schema.py", "cash_flow_forecast.py",
                    "evaluate.py", "foundation_models.py", "classical_models.py",
                    "forecast.py", "common.py")
    document["audit"]["source_file_sha256"] = {
        name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
        for name in source_files
    }
    return document


def write_outputs(run, output_dir, *, source_commit=None):
    """Write after successful assembly/reconciliation; no output on inference failure."""
    document = report_document(run, source_commit=source_commit)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    (target / "forecast.json").write_text(
        json.dumps(document, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    fields = ["month", "line_id", "label", "section", "treatment", "source",
              "model_name", "base_point", "known_overlay", "assumption_overlay",
              "point", "q10", "q50", "q90", "event_ids", "assumption_ids"]
    with (target / "lines.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fields)
        writer.writeheader()
        for row in document["months"]:
            for line in row["lines"]:
                values = {k: line.get(k) for k in fields}
                values["month"] = row["month"]
                for q, label in (("0.1", "q10"), ("0.5", "q50"), ("0.9", "q90")):
                    values[label] = line["quantiles"].get(q)
                values["event_ids"] = "|".join(line["event_ids"])
                values["assumption_ids"] = "|".join(line["assumption_ids"])
                writer.writerow(values)
    totals = ["month", "operating", "investing", "financing",
              "net_cash_movement", "opening_cash", "closing_cash"]
    with (target / "cash_bridge.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, totals)
        writer.writeheader()
        for row in document["months"]:
            writer.writerow({**{k: row[k] for k in totals if k not in row["sections"]},
                             **row["sections"]})
    audit = document["audit"]
    lines = [f"# 12-month Cash Flow Statement ({run.data_kind} inputs)", "",
        f"Origin: {audit['forecast_origin']}; currency: {document['currency']}; schema: {document['schema_version']}.",
        f"Requested model: {audit['requested_model']}; actual models: {', '.join(audit['used_models']) or 'none'}.",
        f"Execution: {audit['execution_kind']}; scenario: {audit['selected_scenario'] or 'none'}.",
        "Input kind is declared by the user, not verified; a forecast is not evidence of accuracy.",
        "Line quantiles are conditional marginal intervals, not aggregate cash intervals.", "",
        "## Cash bridge", "",
        "| Month | Operating | Investing | Financing | Net movement | Opening | Closing |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in document["months"]:
        values = [row["month"], *row["sections"].values(), row["net_cash_movement"],
                  row["opening_cash"], row["closing_cash"]]
        lines.append("| " + " | ".join(values) + " |")
    lines += ["", "## Monthly line detail", "",
        "| Month | Line | Treatment | Source | Base | Known | Assumption | Point | Event ids | Assumption ids |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |"]
    for row in document["months"]:
        for line in row["lines"]:
            values = [row["month"], line["line_id"], line["treatment"], line["source"],
                      line["base_point"], line["known_overlay"], line["assumption_overlay"],
                      line["point"], ", ".join(line["event_ids"]), ", ".join(line["assumption_ids"])]
            lines.append("| " + " | ".join(values) + " |")
    lines += ["", "## Audit", "",
        f"Applied known events: {', '.join(applied for applied in audit['applied_known_event_ids']) or 'none'}.",
        f"Applied assumptions: {', '.join(audit['applied_assumption_ids']) or 'none'}.",
        "All supplied input records and unrounded base forecasts are preserved in forecast.json.", "",
        "| Line | Requested model | Used model | Availability fallback | Training months |",
        "| --- | --- | --- | --- | ---: |"]
    for item in audit["model_runs"]:
        lines.append(f"| {item['line_id']} | {item['requested_model']} | {item['used_model']} | {item['fallback_reason'] or 'none'} | {item['train_months']} |")
    lines += ["", "Excluded inputs:", ""]
    lines += [f"- {item['kind']} {item['input_id']} ({item['line_id']}): {item['reason']}"
              for item in document["excluded_inputs"]] or ["- none"]
    (target / "forecast.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tuple(target / name for name in ("forecast.json", "lines.csv", "cash_bridge.csv", "forecast.md"))

