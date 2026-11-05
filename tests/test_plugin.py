import csv
import json
import shutil
from pathlib import Path

import pytest

import cash_forecast_plugin as plugin
from cash_forecast_plugin import loader, tools

ROOT = Path(plugin.__file__).resolve().parent
EXAMPLE = ROOT / "examples" / "synthetic_company"
LAB = Path(__file__).resolve().parent / "fixtures" / "lab_company"   # Treasury-AI-Lab's 7-line company
TOOL_NAMES = ["cash_forecast_template", "cash_forecast_validate",
              "cash_forecast_run", "cash_forecast_compare"]


class RecordingCtx:
    def __init__(self):
        self.tools, self.skills = [], []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)

    def register_skill(self, **kwargs):
        self.skills.append(kwargs)


def call(handler, **args):
    return json.loads(handler(args))


@pytest.fixture
def company(tmp_path):
    folder = tmp_path / "company"
    shutil.copytree(LAB, folder)
    return folder


def edit(folder, table, change):
    path = folder / f"{table}.csv"
    with path.open(newline="", encoding="utf-8") as file:
        rows = list(csv.reader(file))
    rows = change(rows)
    with path.open("w", newline="", encoding="utf-8") as file:
        csv.writer(file).writerows(rows)


# --- registration ---------------------------------------------------------------
def test_registers_four_tools_and_the_skill():
    ctx = RecordingCtx()
    plugin.register(ctx)
    assert [t["name"] for t in ctx.tools] == TOOL_NAMES
    for tool in ctx.tools:
        assert tool["toolset"] == "treasury_cash_forecast"
        assert tool["schema"]["name"] == tool["name"]
        assert callable(tool["handler"])
    (skill,) = ctx.skills
    assert skill["path"].is_file()


def test_manifest_declares_exactly_the_registered_tools():
    text = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
    declared = [line.strip()[2:] for line in text.splitlines() if line.strip().startswith("- cash_forecast")]
    assert declared == TOOL_NAMES


# --- the numbers match Treasury-AI-Lab -------------------------------------------
# Reference values produced by Treasury-AI-Lab at tag v0.1 with
#   python -m treasury_lab.synthetic_cash_flow --model <model> [--scenario downside]
LAB_FINAL_CLOSING = {("ets", "downside"): "3121307.62", ("baseline", "downside"): "3151576.06"}


@pytest.mark.parametrize("model,scenario", list(LAB_FINAL_CLOSING))
def test_example_reproduces_the_lab_result(company, model, scenario):
    result = call(tools.cash_forecast_run, input_path=str(company), model=model, scenario=scenario)
    assert result["success"], result
    assert result["final_closing_cash"] == LAB_FINAL_CLOSING[(model, scenario)]
    assert result["reconciled"] is True
    assert len(result["cash_bridge"]) == 12
    assert result["opening_cash"] == "1500000.00"
    for name in ("forecast.json", "lines.csv", "cash_bridge.csv", "forecast.md"):
        assert any(Path(f).name == name and Path(f).is_file() for f in result["files"])


def test_cash_bridge_ties_to_the_cent(company):
    from decimal import Decimal
    result = call(tools.cash_forecast_run, input_path=str(company))
    balance = Decimal(result["opening_cash"])
    for month in result["cash_bridge"]:
        net = sum(Decimal(month[s]) for s in ("operating", "investing", "financing"))
        assert net == Decimal(month["net_movement"])
        assert Decimal(month["opening"]) == balance
        balance += net
        assert Decimal(month["closing"]) == balance
    assert Decimal(result["final_closing_cash"]) == balance


def test_base_case_applies_no_assumptions(company):
    base = call(tools.cash_forecast_run, input_path=str(company))
    down = call(tools.cash_forecast_run, input_path=str(company), scenario="downside")
    # downside: 12 x (-25,000 collections - 12,000 contingency) = -444,000
    assert float(base["final_closing_cash"]) - float(down["final_closing_cash"]) == pytest.approx(444000.0)


def test_items_not_yet_known_at_origin_are_left_out(company):
    result = call(tools.cash_forecast_run, input_path=str(company), scenario="downside")
    left_out = {item["id"]: item["reason"] for item in result["left_out_inputs"]}
    assert left_out["future_unconfirmed_funding"] == "not_known_at_origin"
    assert left_out["future_plan_revision"] == "not_available_at_origin"


def test_excel_workbook_gives_the_same_result_as_csv(company, tmp_path):
    made = call(tools.cash_forecast_template, output_dir=str(tmp_path / "t"), format="xlsx")
    assert made["success"], made
    from_xlsx = call(tools.cash_forecast_run, input_path=made["path"], scenario="downside")
    from_csv = call(tools.cash_forecast_run, input_path=str(EXAMPLE), scenario="downside",
                    output_dir=str(tmp_path / "out"))
    assert from_xlsx["success"], from_xlsx
    assert from_xlsx["cash_bridge"] == from_csv["cash_bridge"]


def test_csv_template_round_trip(tmp_path):
    made = call(tools.cash_forecast_template, output_dir=str(tmp_path), format="csv")
    checked = call(tools.cash_forecast_validate, input_path=made["path"])
    assert checked["success"] and checked["ready_to_run"]
    assert checked["scenarios"] == ["downside", "upside"]
    assert checked["shortest_forecast_history_months"] == 36
    assert len(checked["lines"]) == 11
    assert checked["models"]["sarima"]["can_run"] is True


def test_empty_template_is_rejected_with_clear_problems(tmp_path):
    made = call(tools.cash_forecast_template, output_dir=str(tmp_path), format="csv", with_example=False)
    checked = call(tools.cash_forecast_validate, input_path=made["path"])
    assert checked["success"] is False and checked["problems"]


# --- validation -----------------------------------------------------------------
def problems_for(folder):
    result = call(tools.cash_forecast_validate, input_path=str(folder))
    assert result["success"] is False
    return " ".join(result["problems"])


def test_missing_section_is_explained(company):
    edit(company, "lines", lambda rows: [r for r in rows if r[0] != "capex"])
    edit(company, "known_events", lambda rows: [r for r in rows if r[0] != "capex"])
    assert "investing" in problems_for(company)


def test_gap_in_history_is_reported(company):
    def blank(rows):
        rows[1][10] = ""
        return rows
    edit(company, "history", blank)
    assert "Fill the gap" in problems_for(company)


def test_history_must_reach_the_origin_month(company):
    edit(company, "history", lambda rows: [r[:-1] for r in rows])
    assert "origin_month" in problems_for(company)


def test_unknown_line_and_bad_treatment_are_reported(company):
    edit(company, "known_events", lambda rows: rows + [["bonus", "2026-12", "-5000", "", "", ""]])
    def bad(rows):
        rows[1][3] = "guess"
        return rows
    edit(company, "lines", bad)
    text = problems_for(company)
    assert "'bonus' is not in the lines table" in text and "treatment must be" in text


def test_all_problems_are_reported_together(company):
    def bad_inputs(rows):
        return [r if r[0] not in ("currency", "opening_cash") else [r[0], "??", r[2]] for r in rows]
    edit(company, "inputs", bad_inputs)
    result = call(tools.cash_forecast_validate, input_path=str(company))
    assert len(result["problems"]) >= 2


def test_too_little_history_names_the_models_that_can_run(company):
    edit(company, "history", lambda rows: [[r[0]] + r[-14:] for r in rows])
    result = call(tools.cash_forecast_run, input_path=str(company), model="ets")
    assert result["success"] is False
    assert "needs 24 months" in result["problems"][0] and "seasonal_naive" in result["problems"][0]
    ok = call(tools.cash_forecast_run, input_path=str(company), model="seasonal_naive")
    assert ok["success"], ok


def test_unknown_scenario_and_model_are_refused(company):
    result = call(tools.cash_forecast_run, input_path=str(company), scenario="upside", model="prophet")
    assert result["success"] is False and len(result["problems"]) == 2


def test_double_counting_warning_for_known_event_on_forecast_line(company):
    checked = call(tools.cash_forecast_validate, input_path=str(company))
    assert any("customer_receipts" in w and "counted twice" in w for w in checked["warnings"])


def test_actual_data_kind_is_accepted_and_reported(company):
    edit(company, "inputs", lambda rows: [[r[0], "actual", r[2]] if r[0] == "data_kind" else r for r in rows])
    result = call(tools.cash_forecast_run, input_path=str(company))
    assert result["success"] and result["data_kind"] == "actual"
    report = next(Path(f) for f in result["files"] if f.endswith("forecast.md")).read_text()
    assert "(actual inputs)" in report


def test_semicolon_csv_with_european_numbers(company):
    for name in ("inputs", "lines", "history", "known_events", "assumptions"):
        path = company / f"{name}.csv"
        with path.open(newline="", encoding="utf-8") as file:
            rows = list(csv.reader(file))
        if name == "history":
            rows = [rows[0]] + [[r[0]] + [f"{float(v):.2f}".replace(".", ",") for v in r[1:]] for r in rows[1:]]
        with path.open("w", newline="", encoding="utf-8") as file:
            csv.writer(file, delimiter=";").writerows(rows)
    result = call(tools.cash_forecast_run, input_path=str(company), scenario="downside")
    assert result["success"], result
    assert result["final_closing_cash"] == "3121307.62"


@pytest.mark.parametrize("text,value", [("1,234.50", 1234.5), ("1.234,50", 1234.5), ("(500)", -500.0),
                                        ("-12,5", -12.5), ("1,234", 1234.0), (" 7 ", 7.0)])
def test_amount_parsing(text, value):
    assert loader.parse_amount(text) == value


@pytest.mark.parametrize("text", ["2026-09", "2026-09-01", "09/2026", "Sep-26"])
def test_month_parsing(text):
    assert loader.parse_month(text).isoformat() == "2026-09-01"


# --- comparison and robustness ---------------------------------------------------
def test_compare_runs_all_built_in_models(company):
    result = call(tools.cash_forecast_compare, input_path=str(company), scenario="downside")
    assert result["success"], result
    assert [r["model"] for r in result["results"]] == list(tools.SIMPLE_MODELS)
    assert result["skipped"] == []
    assert float(result["spread_of_final_closing_cash"]) > 0


def test_chronos_without_the_package_fails_cleanly(company):
    result = call(tools.cash_forecast_run, input_path=str(company), model="chronos_2")
    if not result["success"]:
        assert "Chronos-2 is not available" in result["error"]


@pytest.mark.parametrize("handler", [tools.cash_forecast_template, tools.cash_forecast_validate,
                                     tools.cash_forecast_run, tools.cash_forecast_compare])
@pytest.mark.parametrize("args", [{}, {"input_path": "/no/such/place", "output_dir": ""},
                                  {"input_path": 5, "model": None, "models": "x,y"}])
def test_handlers_never_raise(handler, args):
    result = json.loads(handler(args))
    assert result["success"] is False and result["error"]


def test_tirex_is_not_offered(company):
    result = call(tools.cash_forecast_run, input_path=str(company), model="tirex_2")
    assert result["success"] is False


# --- the shipped example company --------------------------------------------------
def test_shipped_example_tells_the_intended_story(tmp_path):
    base = call(tools.cash_forecast_run, input_path=str(EXAMPLE), output_dir=str(tmp_path / "b"))
    down = call(tools.cash_forecast_run, input_path=str(EXAMPLE), scenario="downside",
                output_dir=str(tmp_path / "d"))
    assert base["success"] and down["success"]
    assert base["months_below_zero"] == []
    assert down["months_below_zero"] == ["2027-06-01", "2027-07-01"]
    # downside: 12 x (-60,000 collections - 20,000 contingency) = -960,000
    assert float(base["final_closing_cash"]) - float(down["final_closing_cash"]) == pytest.approx(960000.0)
    # the refinancing drawdown was agreed after the forecast date, so it is left out
    (left_out,) = base["left_out_inputs"]
    assert left_out["line_id"] == "loan_repayments" and left_out["reason"] == "not_known_at_origin"
    assert base["assumptions_not_applied"] == 33
    assert base["other_scenarios_available"] == ["downside", "upside"]


def test_ids_are_generated_when_the_template_has_no_id_columns():
    inputs = loader.load_inputs(EXAMPLE)
    assert len({e.event_id for e in inputs.known_events}) == len(inputs.known_events)
    assert all("_row" in e.event_id for e in inputs.known_events)


def test_history_of_known_lines_is_accepted_but_not_used(tmp_path):
    inputs = loader.load_inputs(EXAMPLE)
    assert {row.line_id for row in inputs.history} == {"customer_receipts", "supplier_payments", "operating_expenses"}


def test_old_settings_name_is_still_read(company):
    (company / "inputs.csv").rename(company / "settings.csv")
    assert call(tools.cash_forecast_validate, input_path=str(company))["success"]


def test_example_files_match_the_generator(tmp_path):
    import runpy
    before = {p.name: p.read_bytes() for p in EXAMPLE.glob("*.csv")}
    runpy.run_path(str(ROOT / "examples" / "build_example.py"), run_name="__main__")
    assert {p.name: p.read_bytes() for p in EXAMPLE.glob("*.csv")} == before
