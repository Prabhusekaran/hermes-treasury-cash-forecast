import csv
import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

import cash_forecast_plugin as plugin
from cash_forecast_plugin import loader, tools

ROOT = Path(plugin.__file__).resolve().parent
EXAMPLE = ROOT / "examples" / "synthetic_company"
LAB = Path(__file__).resolve().parent / "fixtures" / "lab_company"   # Treasury-AI-Lab's 7-line company
TOOL_NAMES = ["cash_forecast_template", "cash_forecast_validate",
              "cash_forecast_run", "cash_forecast_compare"]
CENT = Decimal("0.01")


class RecordingCtx:
    def __init__(self):
        self.tools, self.skills = [], []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)

    def register_skill(self, **kwargs):
        self.skills.append(kwargs)


def call(handler, **args):
    return json.loads(handler(args))


def run(folder, **args):
    result = call(tools.cash_forecast_run, input_path=str(folder), **args)
    assert result["success"], result
    return result


def audit_rows(result, scenario, name="lines.csv"):
    path = next(f for f in result["audit_files"] if Path(f).parent.name == scenario and f.endswith(name))
    with open(path, newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def line_rows(result, scenario):
    return {(r["month"], r["line_id"]): r for r in audit_rows(result, scenario)}


@pytest.fixture
def company(tmp_path):
    """Treasury-AI-Lab's own example, in the older template layout."""
    folder = tmp_path / "company"
    shutil.copytree(LAB, folder)
    return folder


@pytest.fixture
def example(tmp_path):
    """The example company the plugin ships."""
    folder = tmp_path / "example"
    shutil.copytree(EXAMPLE, folder)
    return folder


def edit(folder, table, change):
    path = folder / f"{table}.csv"
    with path.open(newline="", encoding="utf-8") as file:
        rows = list(csv.reader(file))
    rows = change(rows)
    with path.open("w", newline="", encoding="utf-8") as file:
        csv.writer(file).writerows(rows)


def problems_for(folder):
    result = call(tools.cash_forecast_validate, input_path=str(folder))
    assert result["success"] is False, result
    return " ".join(result["problems"])


def warnings_for(folder):
    result = call(tools.cash_forecast_validate, input_path=str(folder))
    assert result["success"], result
    return " ".join(result["warnings"])


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
#   python -m treasury_lab.synthetic_cash_flow --model <model> --scenario downside
LAB_DOWNSIDE_CLOSING = {"ets": "3121307.62", "baseline": "3151576.06"}
# First three rows of the lab's cash_bridge.csv for ETS, downside.
LAB_ETS_BRIDGE = [("2026-10-01", "227293.52", "0.00", "-35000.00", "192293.52", "1500000.00", "1692293.52"),
                  ("2026-11-01", "247478.29", "-250000.00", "-35000.00", "-37521.71", "1692293.52", "1654771.81"),
                  ("2026-12-01", "243670.82", "0.00", "-35000.00", "208670.82", "1654771.81", "1863442.63")]


@pytest.mark.parametrize("model", list(LAB_DOWNSIDE_CLOSING))
def test_lab_example_reproduces_the_lab_result(company, model):
    result = run(company, model=model)
    assert list(result["scenarios"]) == ["base", "downside"]
    assert result["scenarios"]["downside"]["final_closing_cash"] == LAB_DOWNSIDE_CLOSING[model]
    assert result["reconciled"] is True and result["opening_cash"] == "1500000.00"
    assert Path(result["main_file"]).name == "cash_forecast.xlsx" and Path(result["main_file"]).is_file()
    for scenario in ("base", "downside"):
        for name in ("forecast.json", "lines.csv", "cash_bridge.csv", "forecast.md"):
            assert any(Path(f).parent.name == scenario and Path(f).name == name and Path(f).is_file()
                       for f in result["audit_files"])


def test_lab_cash_bridge_matches_row_for_row(company):
    rows = audit_rows(run(company), "downside", "cash_bridge.csv")
    got = [(r["month"], r["operating"], r["investing"], r["financing"], r["net_cash_movement"],
            r["opening_cash"], r["closing_cash"]) for r in rows[:3]]
    assert got == LAB_ETS_BRIDGE


def test_lab_base_case_applies_no_scenario(company):
    result = run(company)
    # downside: 12 x (-25,000 collections - 12,000 contingency) = -444,000
    difference = (Decimal(result["scenarios"]["base"]["final_closing_cash"])
                  - Decimal(result["scenarios"]["downside"]["final_closing_cash"]))
    assert difference == Decimal("444000.00")


def test_lab_items_not_yet_known_at_origin_are_left_out(company):
    result = run(company)
    left_out = {item["id"]: item["reason"] for item in result["left_out_inputs"]}
    assert left_out == {"future_unconfirmed_funding": "not_known_at_origin",
                        "future_plan_revision": "not_available_at_origin"}


def test_lab_layout_without_balance_rows_still_runs_with_a_notice(company):
    result = call(tools.cash_forecast_validate, input_path=str(company))
    assert result["success"] and result["opening_cash_taken_from"] == "inputs"
    assert result["history_ties_opening_to_closing"] == "not checked"
    assert any("customer_receipts" in w and "counted twice" in w for w in result["warnings"])


def test_old_table_names_are_still_read(company):
    (company / "inputs.csv").rename(company / "settings.csv")       # scenarios are in assumptions.csv
    result = call(tools.cash_forecast_validate, input_path=str(company))
    assert result["success"] and result["scenarios"] == ["downside"]


# --- reconciliation --------------------------------------------------------------
def test_every_scenario_ties_to_the_cent(example):
    result = run(example)
    for scenario in result["scenarios"]:
        balance = Decimal(result["opening_cash"])
        for row in audit_rows(result, scenario, "cash_bridge.csv"):
            net = sum(Decimal(row[s]) for s in ("operating", "investing", "financing"))
            assert net == Decimal(row["net_cash_movement"])
            assert Decimal(row["opening_cash"]) == balance
            balance += net
            assert Decimal(row["closing_cash"]) == balance
        assert Decimal(result["scenarios"][scenario]["final_closing_cash"]) == balance


# --- the shipped example ----------------------------------------------------------
def test_shipped_example_tells_the_intended_story(example):
    result = run(example)
    assert list(result["scenarios"]) == ["base", "downside", "upside", "with_refinancing"]
    base, down, up = (result["scenarios"][n] for n in ("base", "downside", "upside"))
    assert result["opening_cash"] == "1180000.00"
    assert result["opening_cash_taken_from"] == "history closing_cash row"
    assert base["months_below_zero"] == [] and up["months_below_zero"] == []
    assert base["lowest_closing_cash"]["month"] == "2026-11-01"        # the equipment purchase
    assert down["months_below_zero"] == ["2027-07-01", "2027-08-01", "2027-09-01"]
    assert down["months_below_minimum_cash"] == ["2027-06-01", "2027-07-01", "2027-08-01", "2027-09-01"]
    assert base["months_below_minimum_cash"] == [] and result["minimum_cash"] == "250000.00"
    assert Decimal(up["final_closing_cash"]) > Decimal(base["final_closing_cash"]) > Decimal(down["final_closing_cash"])
    # the refinancing drawdown was agreed after the forecast date, so it is left out
    (left_out,) = result["left_out_inputs"]
    assert left_out["line_id"] == "loan_drawdowns" and left_out["reason"] == "not_known_at_origin"
    assert len(result["closing_cash_by_month"]) == 12
    assert set(result["closing_cash_by_month"][0]) == {"month", "base", "downside", "upside", "with_refinancing"}


def test_template_round_trip_csv(tmp_path):
    made = call(tools.cash_forecast_template, output_dir=str(tmp_path), format="csv")
    checked = call(tools.cash_forecast_validate, input_path=made["path"])
    assert checked["success"] and checked["ready_to_run"]
    assert checked["scenarios"] == ["downside", "upside", "with_refinancing"]
    assert checked["model_chosen_in_inputs"] == "ets" and checked["minimum_cash"] == "250000.00"
    assert checked["shortest_forecast_history_months"] == 36
    assert len(checked["lines"]) == 20
    assert {line["treatment"] for line in checked["lines"]} == {"forecast", "known"}
    assert checked["opening_cash"] == "1180000.00"
    assert checked["history_ties_opening_to_closing"] == "yes"
    assert checked["models"]["sarima"]["can_run"] is True


def test_excel_template_gives_the_same_result_as_csv(example, tmp_path):
    made = call(tools.cash_forecast_template, output_dir=str(tmp_path / "t"), format="xlsx")
    assert made["success"], made
    assert run(made["path"])["closing_cash_by_month"] == run(example)["closing_cash_by_month"]


def test_empty_template_is_rejected_with_clear_problems(tmp_path):
    made = call(tools.cash_forecast_template, output_dir=str(tmp_path), format="csv", with_example=False)
    checked = call(tools.cash_forecast_validate, input_path=made["path"])
    assert checked["success"] is False and checked["problems"]


def test_example_files_match_the_generator():
    import runpy
    before = {p.name: p.read_bytes() for p in EXAMPLE.glob("*.csv")}
    runpy.run_path(str(ROOT / "examples" / "build_example.py"), run_name="__main__")
    assert {p.name: p.read_bytes() for p in EXAMPLE.glob("*.csv")} == before


# --- known amounts on a forecast line: replace or on top ---------------------------
def test_known_amount_replaces_the_model_figure_by_default(example):
    result = run(example)
    rows = line_rows(result, "base")
    october, december = rows[("2026-10-01", "net_salaries")], rows[("2026-12-01", "net_salaries")]
    assert (october["base_point"], october["known_overlay"], october["point"]) == ("0.00", "-286500.00", "-286500.00")
    assert december["known_overlay"] == "0.00" and Decimal(december["base_point"]) < 0     # back to the model
    assert december["point"] == december["base_point"]
    assert {"line_id": "net_salaries", "month": "2026-10"} in result["model_figure_replaced_by_known_amount"]
    assert len(result["model_figure_replaced_by_known_amount"]) == 4


def test_known_amount_on_top_is_added_to_the_model_figure(example):
    row = line_rows(run(example), "base")[("2027-01-01", "receipts_export")]
    assert row["known_overlay"] == "45000.00" and Decimal(row["base_point"]) > 0
    assert Decimal(row["point"]) == Decimal(row["base_point"]) + Decimal("45000.00")


def test_switching_replace_to_on_top_changes_only_that_month(example):
    before = line_rows(run(example), "base")
    def change(rows):
        for r in rows:
            if r[0] == "net_salaries" and r[1] == "2026-10":
                r[3] = "on top"
        return rows
    edit(example, "known_events", change)
    after = line_rows(run(example), "base")
    october = after[("2026-10-01", "net_salaries")]
    assert Decimal(october["base_point"]) < 0 and Decimal(october["point"]) < Decimal("-500000")
    assert after[("2026-11-01", "net_salaries")] == before[("2026-11-01", "net_salaries")]
    assert "net_salaries" in warnings_for(example)          # double-counting warning


def test_the_original_model_figures_are_kept_in_the_audit_file(example):
    result = run(example)
    path = next(f for f in result["audit_files"] if Path(f).parent.name == "base" and f.endswith("forecast.json"))
    document = json.loads(Path(path).read_text())
    salaries = next(f for f in document["inputs"]["base_forecasts"] if f["line_id"] == "net_salaries")
    assert salaries["point"][0] < 0                         # what the model would have said for October
    assert len(document["inputs"]["model_forecast_replaced_by_known_amount"]) == 4


def test_unknown_effect_is_explained(example):
    edit(example, "known_events", lambda rows: rows + [["capex", "2027-08", "-1", "maybe", "", ""]])
    assert "effect must be 'replace' or 'on top'" in problems_for(example)


def test_model_continues_from_the_level_of_the_last_known_amount(example):
    result = run(example)
    rows = line_rows(result, "base")
    path = next(f for f in result["audit_files"] if Path(f).parent.name == "base" and f.endswith("forecast.json"))
    document = json.loads(Path(path).read_text())
    model = next(f for f in document["inputs"]["base_forecasts"] if f["line_id"] == "net_salaries")["point"]
    shift = Decimal("-288000.00") - Decimal(str(model[1])).quantize(CENT)      # November: known minus model
    for index, month in enumerate(["2026-12-01", "2027-01-01", "2027-09-01"], start=2):
        index = 11 if month == "2027-09-01" else index
        expected = (Decimal(str(model[index])) + Decimal("-288000.00") - Decimal(str(model[1]))).quantize(CENT)
        assert Decimal(rows[(month, "net_salaries")]["point"]) == expected
    december = Decimal(rows[("2026-12-01", "net_salaries")]["point"])
    assert Decimal("-290000") < december < Decimal("-288000")      # carries on, no step back to -282,658
    assert shift < 0
    assert {"line_id": "net_salaries", "known_months": 2, "level_shift": f"{float(Decimal('-288000') - Decimal(str(model[1]))):.2f}"} \
        in result["model_continued_from_known_amounts"]


def test_known_amount_in_a_later_month_does_not_move_the_other_months(example):
    before = line_rows(run(example), "base")
    edit(example, "known_events", lambda rows: rows + [["utilities_insurance", "2027-03", "-70000", "", "", ""]])
    after = line_rows(run(example), "base")
    assert after[("2027-03-01", "utilities_insurance")]["point"] == "-70000.00"
    for month in ("2027-02-01", "2027-04-01"):
        assert after[(month, "utilities_insurance")] == before[(month, "utilities_insurance")]


# --- scenarios in percent -----------------------------------------------------------
def test_percent_is_taken_from_the_base_case_figure(example):
    result = run(example)
    base, down = line_rows(result, "base"), line_rows(result, "downside")
    for (month, line_id), row in down.items():
        before = Decimal(base[(month, line_id)]["point"])
        change = Decimal(row["assumption_overlay"])
        if line_id == "receipts_domestic":
            assert change == (before * Decimal("-4") / 100).quantize(CENT)
        elif line_id == "receipts_export":
            assert change == (before * Decimal("-8") / 100).quantize(CENT)
        elif line_id == "suppliers_materials":      # +4% from January 2027 only: payments get larger
            expected = (before * Decimal("4") / 100).quantize(CENT) if month >= "2027-01" else 0
            assert change == expected and change <= 0
        elif line_id == "management_reserve":
            assert change == Decimal("-20000.00")
        else:
            assert change == 0
        assert Decimal(row["point"]) == before + change
    effect = result["scenarios"]["downside"]["effect_by_line"]
    assert (Decimal(result["scenarios"]["base"]["final_closing_cash"])
            + Decimal(effect["total_effect_on_final_closing_cash"])
            == Decimal(result["scenarios"]["downside"]["final_closing_cash"]))


def test_scenarios_do_not_change_the_model_or_the_schedule(example):
    result = run(example)
    def untouched(scenario):
        return [(r["month"], r["line_id"], r["base_point"], r["known_overlay"]) for r in audit_rows(result, scenario)]
    assert untouched("base") == untouched("downside") == untouched("upside")


def test_only_the_scenarios_asked_for_are_run(example):
    result = run(example, scenarios=["upside"])
    assert list(result["scenarios"]) == ["base", "upside"]
    assert result["scenarios_not_run"] == ["downside", "with_refinancing"]
    assert list(run(example, scenarios=[])["scenarios"]) == ["base"]
    assert list(run(example, scenario="downside")["scenarios"]) == ["base", "downside"]


def test_unknown_scenario_and_model_are_refused(example):
    result = call(tools.cash_forecast_run, input_path=str(example), scenarios=["boom"], model="prophet")
    assert result["success"] is False and len(result["problems"]) == 2


@pytest.mark.parametrize("row,message", [
    (["downside", "receipts_export", "5", "100", "", "", ""], "either 'percent' or 'amount'"),
    (["downside", "receipts_export", "", "", "", "", ""], "either 'percent' or 'amount'"),
    (["downside", "receipts_export", "250", "", "", "", ""], "outside -100 to 100"),
    (["downside", "receipts_export", "5", "", "2027-05", "2027-01", ""], "to_month is before from_month"),
    (["downside", "no_such_line", "5", "", "", "", ""], "not one of the lines in the history sheet"),
    (["base", "receipts_export", "5", "", "", "", ""], "choose another name"),
])
def test_bad_scenario_rows_are_explained(example, row, message):
    edit(example, "scenarios", lambda rows: rows + [row])
    assert message in problems_for(example)


def test_percent_sign_and_decimal_comma_are_read(example):
    edit(example, "scenarios", lambda rows: rows + [["mild", "receipts_export", "-2,5 %", "", "", "", ""]])
    result = run(example, scenarios=["mild"])
    assert Decimal(result["scenarios"]["mild"]["effect_by_line"]["receipts_export"]) < 0


def test_a_left_out_amount_can_be_added_back_as_a_scenario(example):
    result = run(example)
    for row in result["closing_cash_by_month"]:
        difference = Decimal(row["with_refinancing"]) - Decimal(row["base"])
        assert difference == (Decimal("1000000.00") if row["month"] >= "2027-03" else 0)


# --- model and minimum cash from the inputs sheet -------------------------------------
def test_model_can_be_chosen_in_the_inputs_sheet(example):
    edit(example, "inputs", lambda rows: [[r[0], "seasonal_naive", r[2]] if r[0] == "model" else r for r in rows])
    assert run(example)["model"] == "seasonal_naive"
    assert run(example, model="baseline")["model"] == "baseline"       # asking Hermes wins


def test_unknown_model_in_the_inputs_sheet_is_refused(example):
    edit(example, "inputs", lambda rows: [[r[0], "prophet", r[2]] if r[0] == "model" else r for r in rows])
    assert "model 'prophet' is not available" in problems_for(example)
    assert call(tools.cash_forecast_run, input_path=str(example))["success"] is False


def test_minimum_cash_is_optional(example):
    edit(example, "inputs", lambda rows: [r for r in rows if r[0] not in ("minimum_cash", "model")])
    result = run(example)
    assert result["minimum_cash"] is None and result["model"] == "ets"
    assert "months_below_minimum_cash" not in result["scenarios"]["base"]


# --- opening and closing cash in the history ----------------------------------------
def test_opening_cash_comes_from_the_closing_cash_row(example):
    def change(rows):
        rows[-1][-1] = "1000000"
        return rows
    edit(example, "history", change)
    result = call(tools.cash_forecast_validate, input_path=str(example))
    assert result["opening_cash"] == "1000000.00"
    assert result["history_ties_opening_to_closing"] == "no, see warnings"
    assert any("does not tie" in w and "2026-09" in w for w in result["warnings"])


def test_conflicting_opening_cash_is_refused(example):
    edit(example, "inputs", lambda rows: rows + [["opening_cash", "5", ""]])
    assert "differs" in problems_for(example)


def test_opening_cash_is_required_somewhere(example):
    edit(example, "history", lambda rows: [r for r in rows if r[0] != "closing_cash"])
    assert "No opening cash" in problems_for(example)


def test_history_break_between_months_is_reported(example):
    def change(rows):
        rows[1][5] = str(float(rows[1][5]) + 1000)       # an opening_cash value
        return rows
    edit(example, "history", change)
    assert "opening differs" in warnings_for(example)


# --- keeping the sheets in step -------------------------------------------------------
def add_line(rows, *definition):
    blanks = [""] * (len(rows[0]) - 4)
    return rows[:-1] + [list(definition) + blanks] + rows[-1:]      # keep closing_cash last


def test_line_removed_from_history_but_left_elsewhere_is_reported(example):
    edit(example, "history", lambda rows: [r for r in rows if r[0] != "capex"])
    edit(example, "scenarios", lambda rows: rows + [["downside", "capex", "10", "", "", "", ""]])
    text = problems_for(example)
    assert "known_events row" in text and "scenarios row" in text
    assert text.count("'capex' is not one of the lines in the history sheet") == 3


def test_new_forecast_line_without_history_is_reported(example):
    edit(example, "history", lambda rows: add_line(rows, "licence_income", "Licence income", "operating", "forecast"))
    assert "history 'licence_income': no values" in problems_for(example)


def test_history_needs_the_line_columns(example):
    edit(example, "history", lambda rows: [[r[0]] + r[4:] for r in rows])
    assert "missing column(s): label, section, treatment" in problems_for(example)


def test_recap_lists_known_amounts_and_scenarios_per_line(example):
    result = call(tools.cash_forecast_validate, input_path=str(example))
    lines = {line["line_id"]: line for line in result["lines"]}
    assert lines["capex"]["known_amounts"] == 2 and lines["capex"]["known_total"] == "-570000.00"
    assert lines["net_salaries"]["known_amounts_replacing_the_model"] == 2
    assert lines["receipts_export"]["known_amounts_on_top_of_the_model"] == 3
    assert lines["receipts_export"]["scenarios"] == ["downside", "upside"]
    assert "known_amounts" not in lines["loan_drawdowns"]            # its only amount is not yet known
    assert "still apply" in result["check_with_the_user"]


def test_empty_template_has_no_example_rows_to_clear(tmp_path):
    made = call(tools.cash_forecast_template, output_dir=str(tmp_path), format="csv", with_example=False)
    for name in ("known_events", "scenarios"):
        with (Path(made["path"]) / f"{name}.csv").open(newline="", encoding="utf-8") as file:
            assert len(list(csv.reader(file))) == 1


def test_new_known_line_without_history_or_schedule_gets_warnings(example):
    edit(example, "history", lambda rows: add_line(rows, "grant", "Government grant", "operating", "known"))
    text = warnings_for(example)
    assert "could not be tied" in text and "grant" in text
    assert "Known line 'grant' has no scheduled amount" in text


def test_missing_section_is_explained(example):
    investing = {"capex", "asset_disposals", "interest_received"}
    for table in ("history", "known_events"):
        edit(example, table, lambda rows: [r for r in rows if r[0] not in investing])
    assert "investing" in problems_for(example)


def test_gap_in_history_is_reported(example):
    def blank(rows):
        row = next(r for r in rows if r[0] == "receipts_domestic")
        row[10] = ""
        return rows
    edit(example, "history", blank)
    assert "Fill the gap" in problems_for(example)


def test_history_must_reach_the_origin_month(example):
    edit(example, "history", lambda rows: [r[:-1] for r in rows])
    assert "origin_month" in problems_for(example)


def test_bad_treatment_is_reported(example):
    def bad(rows):
        next(r for r in rows if r[0] == "receipts_domestic")[3] = "guess"
        return rows
    edit(example, "history", bad)
    assert "treatment must be forecast or known" in problems_for(example)


def test_all_problems_are_reported_together(example):
    edit(example, "inputs", lambda rows: [[r[0], "??", r[2]] if r[0] == "currency" else r for r in rows])
    edit(example, "known_events", lambda rows: rows + [["bonus", "2026-12", "-5000", "", "", ""]])
    result = call(tools.cash_forecast_validate, input_path=str(example))
    assert len(result["problems"]) >= 2


def test_too_little_history_names_the_models_that_can_run(example):
    edit(example, "history", lambda rows: [r[:4] + r[-14:] for r in rows])
    result = call(tools.cash_forecast_run, input_path=str(example), model="ets")
    assert result["success"] is False
    assert "needs 24 months" in result["problems"][0] and "seasonal_naive" in result["problems"][0]
    assert run(example, model="seasonal_naive")["model"] == "seasonal_naive"


def test_actual_data_kind_is_accepted_and_reported(example):
    edit(example, "inputs", lambda rows: [[r[0], "actual", r[2]] if r[0] == "data_kind" else r for r in rows])
    result = run(example)
    assert result["data_kind"] == "actual"
    report = next(Path(f) for f in result["audit_files"] if f.endswith("forecast.md")).read_text()
    assert "(actual inputs)" in report


def test_semicolon_csv_with_european_numbers(example):
    expected = run(example)["closing_cash_by_month"]
    for name in ("inputs", "history", "known_events", "scenarios"):
        path = example / f"{name}.csv"
        with path.open(newline="", encoding="utf-8") as file:
            rows = list(csv.reader(file))
        if name == "history":
            rows = [rows[0]] + [r[:4] + [f"{float(v):.2f}".replace(".", ",") for v in r[4:]] for r in rows[1:]]
        with path.open("w", newline="", encoding="utf-8") as file:
            csv.writer(file, delimiter=";").writerows(rows)
    assert run(example)["closing_cash_by_month"] == expected


@pytest.mark.parametrize("text,value", [("1,234.50", 1234.5), ("1.234,50", 1234.5), ("(500)", -500.0),
                                        ("-12,5", -12.5), ("1,234", 1234.0), (" 7 ", 7.0)])
def test_amount_parsing(text, value):
    assert loader.parse_amount(text) == value


@pytest.mark.parametrize("text", ["2026-09", "2026-09-01", "09/2026", "Sep-26"])
def test_month_parsing(text):
    assert loader.parse_month(text).isoformat() == "2026-09-01"


def test_ids_are_generated_when_the_template_has_no_id_columns():
    inputs = loader.load_inputs(EXAMPLE)
    assert len({e.event_id for e in inputs.known_events}) == len(inputs.known_events)
    assert all("_row" in e.event_id for e in inputs.known_events)


def test_only_forecast_lines_reach_the_model_but_all_history_is_kept():
    inputs = loader.load_inputs(EXAMPLE)
    forecast_lines = {l.line_id for l in inputs.schema.lines if l.treatment == "forecast"}
    assert {row.line_id for row in inputs.history} == forecast_lines and len(forecast_lines) == 10
    assert len(inputs.actuals) == 20


# --- the Excel workbook ----------------------------------------------------------------
def test_workbook_has_a_summary_and_one_statement_per_scenario(example):
    from openpyxl import load_workbook
    result = run(example)
    book = load_workbook(result["main_file"])
    assert book.sheetnames == ["Summary", "How this is built", "Base case", "downside", "upside",
                               "with_refinancing", "Where numbers come from", "Notes"]
    summary = {row[0]: row for row in book["Summary"].iter_rows(values_only=True) if row[0]}
    assert summary["Closing cash by month"][1:4] == ("Base case", "downside", "upside")
    for column, scenario in enumerate(("base", "downside", "upside"), start=1):
        assert summary["Final closing cash"][column] == pytest.approx(
            float(result["scenarios"][scenario]["final_closing_cash"]))
    assert summary["Months with closing cash below zero"][1:4] == ("none", "2027-07 to 2027-09", "none")
    assert summary["Months below your minimum cash of 250,000.00"][1:4] == ("none", "2027-06 to 2027-09", "none")
    assert summary["Loan drawdowns"][1:4] == ("2027-03", 1000000, "only known after the forecast date "
                                                                  "(known from 2026-11)")
    how = " ".join(str(row[1]) for row in book["How this is built"].iter_rows(values_only=True) if row[1])
    assert "ETS (exponential smoothing)" in how and "did not calculate or adjust any number" in how


@pytest.mark.parametrize("sheet_name,scenario", [("Base case", "base"), ("downside", "downside")])
def test_statement_runs_from_actuals_into_the_forecast(example, sheet_name, scenario):
    from openpyxl import load_workbook
    result = run(example)
    sheet = load_workbook(result["main_file"])[sheet_name]
    rows = {row[0]: row for row in sheet.iter_rows(values_only=True) if row[0]}
    kinds = list(sheet.iter_rows(min_row=5, max_row=5, values_only=True))[0][2:]
    assert list(kinds) == ["Actual"] * 12 + ["Forecast"] * 12
    opening, net, closing = rows["Opening cash"][2:], rows["Net cash flow"][2:], rows["Closing cash"][2:]
    for i in range(24):
        assert opening[i] + net[i] == pytest.approx(closing[i], abs=0.005)
        if i:
            assert opening[i] == pytest.approx(closing[i - 1], abs=0.005)
    assert closing[11] == 1180000 and opening[12] == 1180000
    assert closing[-1] == pytest.approx(float(result["scenarios"][scenario]["final_closing_cash"]))
    sections = [rows[f"Net cash from {s} activities"][2:] for s in ("operating", "investing", "financing")]
    for i in range(24):
        assert sum(s[i] for s in sections) == pytest.approx(net[i], abs=0.005)


def test_statement_shows_the_mix_of_known_and_forecast_months(example):
    from openpyxl import load_workbook
    sheet = load_workbook(run(example)["main_file"])["Base case"]
    rows = {str(row[0].value).strip(): row for row in sheet.iter_rows() if row[0].value}
    def shading(label):
        return [cell.fill.fgColor.rgb[-6:] if cell.fill.fill_type else None for cell in rows[label][14:26]]
    assert rows["Net salaries"][1].value == "known 2026-10 to 2026-11, then forecast from that level"
    assert shading("Net salaries") == ["E2EFDA"] * 2 + [None] * 10
    assert rows["Receipts from customers, export"][1].value == "forecast, plus known on top 2027-01 to 2027-03"
    assert shading("Receipts from customers, export") == [None] * 3 + ["FCE4D6"] * 3 + [None] * 6
    assert rows["Rent and leases"][1].value == "known" and shading("Rent and leases") == ["E2EFDA"] * 12
    assert rows["Receipts from customers, domestic"][1].value == "forecast"
    assert shading("Receipts from customers, domestic") == [None] * 12


def test_workbook_shows_cents_and_marks_scenario_changes(example):
    from openpyxl import load_workbook
    book = load_workbook(run(example)["main_file"])
    sheet = book["downside"]
    rows = {str(row[0].value).strip(): row for row in sheet.iter_rows() if row[0].value}
    assert rows["Closing cash"][5].number_format == "#,##0.00;[Red]-#,##0.00"
    fills = [cell.fill.fgColor.rgb[-6:] if cell.fill.fill_type else None
             for cell in rows["Receipts from customers, domestic"][14:26]]
    assert fills == ["E4DFEC"] * 12                                    # changed by the scenario in every month
    materials = [cell.fill.fgColor.rgb[-6:] if cell.fill.fill_type else None
                 for cell in rows["Payments to suppliers, materials"][14:26]]
    assert materials == [None] * 3 + ["E4DFEC"] * 9                    # +4% from January 2027 only
    for label in ("Net cash from operating activities", "Net cash flow"):
        for cell in rows[label][2:14]:                                 # actual months: no float noise
            assert round(cell.value, 2) == cell.value
    detail = list(book["Where numbers come from"].iter_rows(values_only=True))
    october = next(r for r in detail if r[0] == "Base case" and r[1] == "2026-10" and r[2] == "Net salaries")
    assert october[4] == 0 and october[5] == -286500 and october[9] < 0 and "replaced" in october[10]


def test_excel_template_has_the_reminder_and_dropdowns(tmp_path):
    from openpyxl import load_workbook
    made = call(tools.cash_forecast_template, output_dir=str(tmp_path), format="xlsx")
    book = load_workbook(made["path"])
    assert book.sheetnames == ["instructions", "inputs", "history", "known_events", "scenarios"]
    for name in ("known_events", "scenarios"):
        assert str(book[name]["A1"].value).startswith("NOTE:") and book[name]["A1"].fill.fgColor.rgb[-6:] == "C00000"
    assert book["instructions"]["A2"].value == "FIRST"
    lists = {str(v.sqref): v.formula1 for name in ("inputs", "history", "known_events")
             for v in book[name].data_validations.dataValidation}
    assert '"forecast,known"' in lists.values() and '"replace,on top"' in lists.values()
    assert '"operating,investing,financing"' in lists.values() and '"synthetic,actual,unspecified"' in lists.values()
    empty = call(tools.cash_forecast_template, output_dir=str(tmp_path / "e"), format="xlsx", with_example=False)
    blank = load_workbook(empty["path"])
    assert blank["known_events"]["A1"].value == "line_id" and blank["known_events"].max_row == 1


# --- comparison and robustness -----------------------------------------------------------
def test_compare_runs_all_built_in_models(example):
    result = call(tools.cash_forecast_compare, input_path=str(example), scenario="downside")
    assert result["success"], result
    assert [r["model"] for r in result["results"]] == list(tools.SIMPLE_MODELS)
    assert result["skipped"] == [] and float(result["spread_of_final_closing_cash"]) > 0
    notes = {r["model"]: r.get("model_notes") for r in result["results"]}
    assert notes["ets"] is None and any("EstimationWarning" in n for n in notes["sarima"])
    base = call(tools.cash_forecast_compare, input_path=str(example), models=["ets"])
    assert base["results"][0]["final_closing_cash"] == run(example)["scenarios"]["base"]["final_closing_cash"]


def test_chronos_without_the_package_fails_cleanly(example):
    result = call(tools.cash_forecast_run, input_path=str(example), model="chronos_2")
    if not result["success"]:
        assert "Chronos-2 is not available" in result["error"]


@pytest.mark.parametrize("handler", [tools.cash_forecast_template, tools.cash_forecast_validate,
                                     tools.cash_forecast_run, tools.cash_forecast_compare])
@pytest.mark.parametrize("args", [{}, {"input_path": "/no/such/place", "output_dir": ""},
                                  {"input_path": 5, "model": None, "models": "x,y", "scenarios": 3}])
def test_handlers_never_raise(handler, args):
    result = json.loads(handler(args))
    assert result["success"] is False and result["error"]


def test_tirex_is_not_offered(example):
    assert call(tools.cash_forecast_run, input_path=str(example), model="tirex_2")["success"] is False
