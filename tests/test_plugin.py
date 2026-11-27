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
# Only the deterministic models are compared: they give the same figures on every machine. ETS is not
# in this list because the plugin starts it differently from the lab (see engine/__init__.py, change 5).
LAB_DOWNSIDE_CLOSING = {"baseline": "3151576.06", "seasonal_drift": "3090164.17",
                        "seasonal_naive": "2721509.05", "previous_month": "2948596.68"}
# First three rows of the lab's cash_bridge.csv for the baseline model, downside.
LAB_BASELINE_BRIDGE = [("2026-10-01", "232421.83", "0.00", "-35000.00", "197421.83", "1500000.00", "1697421.83"),
                       ("2026-11-01", "251892.86", "-250000.00", "-35000.00", "-33107.14", "1697421.83", "1664314.69"),
                       ("2026-12-01", "248311.54", "0.00", "-35000.00", "213311.54", "1664314.69", "1877626.23")]


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
    rows = audit_rows(run(company, model="baseline"), "downside", "cash_bridge.csv")
    got = [(r["month"], r["operating"], r["investing"], r["financing"], r["net_cash_movement"],
            r["opening_cash"], r["closing_cash"]) for r in rows[:3]]
    assert got == LAB_BASELINE_BRIDGE


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


# --- the same input gives the same forecast ------------------------------------------------
def test_ets_forecast_does_not_jump_when_the_input_changes_by_a_rounding_error(example):
    """Guards the fix from the first live test, where two machines disagreed by 100,000 on closing cash."""
    before = run(example)["scenarios"]["base"]["final_closing_cash"]
    def nudge(rows):
        return [rows[0]] + [r[:4] + [repr(float(v) * (1 + 1e-12)) if v else v for v in r[4:]] for r in rows[1:]]
    edit(example, "history", nudge)
    after = run(example)["scenarios"]["base"]["final_closing_cash"]
    assert abs(Decimal(after) - Decimal(before)) < Decimal("25")


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
    assert list(result["scenarios"]) == ["base", "customer_pays_late", "downside", "upside", "with_refinancing"]
    base, down, up = (result["scenarios"][n] for n in ("base", "downside", "upside"))
    assert result["opening_cash"] == "1180000.00"
    assert result["opening_cash_taken_from"] == "history closing_cash row"
    assert base["months_below_zero"] == [] and up["months_below_zero"] == []
    assert base["lowest_closing_cash"]["month"] == "2026-11-01"        # the equipment purchase
    assert down["months_below_zero"] == ["2027-09-01"]
    assert down["months_below_minimum_cash"] == ["2027-06-01", "2027-07-01", "2027-08-01", "2027-09-01"]
    assert base["months_below_minimum_cash"] == [] and result["minimum_cash"] == "250000.00"
    assert Decimal(up["final_closing_cash"]) > Decimal(base["final_closing_cash"]) > Decimal(down["final_closing_cash"])
    # the refinancing drawdown was agreed after the forecast date, so it is left out
    (left_out,) = result["left_out_inputs"]
    assert left_out["line_id"] == "loan_drawdowns" and left_out["reason"] == "not_known_at_origin"
    assert len(result["closing_cash_by_month"]) == 12
    assert set(result["closing_cash_by_month"][0]) == {"month", "base", "customer_pays_late", "downside", "upside",
                                                       "with_refinancing"}


def test_template_round_trip_csv(tmp_path):
    made = call(tools.cash_forecast_template, output_dir=str(tmp_path), format="csv")
    checked = call(tools.cash_forecast_validate, input_path=made["path"])
    assert checked["success"] and checked["ready_to_run"]
    assert checked["scenarios"] == ["customer_pays_late", "downside", "upside", "with_refinancing"]
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
    assert result["scenarios_not_run"] == ["customer_pays_late", "downside", "with_refinancing"]
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


def test_a_late_payment_moves_cash_between_months_but_not_the_year_end(example):
    result = run(example)
    late = result["scenarios"]["customer_pays_late"]
    assert late["final_closing_cash"] == result["scenarios"]["base"]["final_closing_cash"]
    assert result["scenarios"]["base"]["lowest_closing_cash"]["month"] == "2026-11-01"
    assert late["lowest_closing_cash"]["month"] == "2027-06-01"        # the low point moves to June
    for row in result["closing_cash_by_month"]:
        difference = Decimal(row["customer_pays_late"]) - Decimal(row["base"])
        assert difference == (Decimal("-300000.00") if row["month"] == "2027-06-01" else 0)
    assert Decimal(late["effect_by_line"]["total_effect_on_final_closing_cash"]) == 0


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
    assert lines["receipts_export"]["scenarios"] == ["customer_pays_late", "downside", "upside"]
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
    assert book.sheetnames == ["Summary", "How this is built", "Base case", "customer_pays_late", "downside",
                               "upside", "with_refinancing", "Where numbers come from", "Notes"]
    summary = {row[0]: row for row in book["Summary"].iter_rows(values_only=True) if row[0]}
    assert summary["Closing cash by month"][1:5] == ("Base case", "customer_pays_late", "downside", "upside")
    for column, scenario in enumerate(("base", "customer_pays_late", "downside", "upside"), start=1):
        assert summary["Final closing cash"][column] == pytest.approx(
            float(result["scenarios"][scenario]["final_closing_cash"]))
    assert summary["Months with closing cash below zero"][1:5] == ("none", "none", "2027-09", "none")
    assert summary["Months below your minimum cash of 250,000.00"][1:5] == ("none", "none", "2027-06 to 2027-09", "none")
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


# --- libraries not visible to a Hermes process that was not restarted ----------------------
def test_library_versions_are_reported(example):
    from openpyxl import load_workbook
    result = run(example)
    assert set(result["library_versions"]) == {"python", "statsmodels", "numpy", "scipy", "openpyxl"}
    notes = {row[0]: row[1] for row in load_workbook(result["main_file"])["Notes"].iter_rows(values_only=True) if row[0]}
    assert "statsmodels" in notes["Library versions"] and "numpy" in notes["Library versions"]


def test_missing_model_library_asks_for_a_restart_and_does_not_fall_back(example, monkeypatch):
    monkeypatch.setattr(tools, "_missing_libraries", lambda: ["statsmodels"])
    import sys
    monkeypatch.setitem(sys.modules, "statsmodels.tsa.holtwinters", None)        # importing it now fails
    checked = call(tools.cash_forecast_validate, input_path=str(example))
    assert checked["success"] and checked["ready_to_run"] is False
    assert checked["libraries_missing_in_this_session"] == ["statsmodels"]
    assert "hermes gateway restart" in checked["warnings"][0] and "Stop" in checked["next_step"]
    assert "Desktop app" in checked["warnings"][0] and "hermes serve" in checked["warnings"][0]
    result = call(tools.cash_forecast_run, input_path=str(example))
    assert result["success"] is False
    assert "hermes gateway restart" in result["error"] and "pip install" in result["error"]
    assert not (example / "cash_forecast_output").exists()


def test_missing_excel_library_stops_before_anything_is_written(example, tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_missing_libraries", lambda: ["openpyxl"])
    made = call(tools.cash_forecast_template, output_dir=str(tmp_path / "t"), format="xlsx")
    assert made["success"] is False and "hermes gateway restart" in made["error"]
    result = call(tools.cash_forecast_run, input_path=str(example))
    assert result["success"] is False and "openpyxl" in result["error"]
    assert not (example / "cash_forecast_output").exists()
    assert call(tools.cash_forecast_template, output_dir=str(tmp_path / "c"), format="csv")["success"]


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


# --- 0.2.0: every figure can be traced to the row of the input it came from ---------------------
def detail_rows(result):
    from openpyxl import load_workbook
    rows = list(load_workbook(result["main_file"])["Where numbers come from"].iter_rows(values_only=True))
    assert rows[0][-1] == "Source row"
    return {(r[0], r[1], r[2]): r[-1] for r in rows[1:]}


def csv_row(folder, table, number):
    """Row `number` of a CSV file, counted the way a spreadsheet shows it (1 = the headings)."""
    with (folder / f"{table}.csv").open(newline="", encoding="utf-8") as file:
        return list(csv.reader(file))[number - 1]


def test_every_forecast_figure_names_the_input_rows_it_came_from(example):
    traced = detail_rows(run(example))
    assert all(traced.values())                                      # no figure without a source row
    assert traced[("Base case", "2026-10", "Net salaries")] == "known_events row 2"
    assert csv_row(example, "known_events", 2)[:3] == ["net_salaries", "2026-10", "-286500"]
    assert traced[("Base case", "2026-12", "Net salaries")] == "history row 8"       # the model again
    assert csv_row(example, "history", 8)[0] == "net_salaries"
    assert traced[("downside", "2027-01", "Receipts from customers, export")] == \
        "history row 4; known_events row 38; scenarios row 3"
    assert csv_row(example, "history", 4)[0] == "receipts_export"
    assert csv_row(example, "known_events", 38)[:4] == ["receipts_export", "2027-01", "45000", "on top"]
    assert csv_row(example, "scenarios", 3)[:3] == ["downside", "receipts_export", "-8"]
    # two scenario rows on one line, each in its own month
    assert traced[("customer_pays_late", "2027-06", "Receipts from customers, export")] == \
        "history row 4; scenarios row 9"
    assert traced[("customer_pays_late", "2027-07", "Receipts from customers, export")] == \
        "history row 4; scenarios row 10"
    # a known line names only the schedule; the base case never names a scenarios row
    assert traced[("Base case", "2026-10", "Rent and leases")] == "known_events row 4"
    assert not any("scenarios" in rows for (scenario, _, _), rows in traced.items() if scenario == "Base case")


def test_source_rows_are_the_rows_excel_shows(tmp_path):
    from openpyxl import load_workbook
    made = call(tools.cash_forecast_template, output_dir=str(tmp_path), format="xlsx")
    book = load_workbook(made["path"])
    # The example workbook has a NOTE in row 1 and the headings in row 2, so the first amount is in row 3.
    assert [book["known_events"].cell(3, column).value for column in (1, 2, 3)] == ["net_salaries", "2026-10", -286500]
    traced = detail_rows(run(made["path"]))
    assert traced[("Base case", "2026-10", "Net salaries")] == "known_events row 3"
    assert traced[("downside", "2027-01", "Receipts from customers, export")] == \
        "history row 4; known_events row 39; scenarios row 4"
    # An empty row in the middle of a sheet moves the rows below it, and the reference follows.
    book["scenarios"].insert_rows(4)
    shifted = tmp_path / "shifted.xlsx"
    book.save(shifted)
    assert [book["scenarios"].cell(5, column).value for column in (1, 2, 3)] == ["downside", "receipts_export", -8]
    assert detail_rows(run(shifted, output_dir=str(tmp_path / "out")))[
        ("downside", "2027-01", "Receipts from customers, export")] == \
        "history row 4; known_events row 39; scenarios row 5"


def test_a_problem_names_the_row_excel_shows(tmp_path):
    from openpyxl import load_workbook
    made = call(tools.cash_forecast_template, output_dir=str(tmp_path), format="xlsx")
    book = load_workbook(made["path"])
    book["scenarios"].cell(4, 2).value = "no_such_line"
    broken = tmp_path / "broken.xlsx"
    book.save(broken)
    assert "scenarios row 4: 'no_such_line'" in problems_for(broken)


def test_summary_names_the_row_of_each_scenario_rule_and_left_out_amount(example):
    from openpyxl import load_workbook
    rows = list(load_workbook(run(example)["main_file"])["Summary"].iter_rows(values_only=True))
    rule = next(r for r in rows if r[0] == "downside" and r[1] == "Receipts from customers, export")
    assert rule[2] == "-8%" and rule[5] == "scenarios row 3" and rule[6] == "Weaker export demand"
    left_out = next(r for r in rows if r[0] == "Loan drawdowns")
    assert left_out[4] == "known_events row 41" and left_out[5].startswith("Refinancing drawdown")
    assert csv_row(example, "known_events", 41)[:3] == ["loan_drawdowns", "2027-03", "1000000"]


# --- 0.2.0: the forecast says which run and which input produced it -------------------------------
def test_version_is_the_same_everywhere():
    import re
    manifest = re.search(r"^version: (\S+)", (ROOT / "plugin.yaml").read_text(encoding="utf-8"), re.M).group(1)
    project = re.search(r'^version = "(\S+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
    assert manifest == project == tools.VERSION == "0.2.1"


def test_run_reports_what_identifies_it(example):
    import re
    from openpyxl import load_workbook
    result = run(example)
    identity = result["forecast_identity"]
    assert list(identity) == ["Plugin version", "Run at", "Model", "Model settings", "History used by the model",
                              "Input", "Input fingerprint", "Libraries"]
    assert identity["Plugin version"] == f"Treasury Cash Forecast {tools.VERSION}"
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d \S.* \(UTC[+-]\d{4}\)", identity["Run at"])
    assert identity["Model"] == "ets" and "heuristic start" in identity["Model settings"]
    assert identity["History used by the model"] == "36 months, 2023-10 to 2026-09"
    assert identity["Input"] == "example"
    assert re.fullmatch(r"[0-9a-f]{12}", identity["Input fingerprint"])
    assert "statsmodels" in identity["Libraries"]
    assert call(tools.cash_forecast_validate, input_path=str(example))["input_fingerprint"] == identity["Input fingerprint"]
    book = load_workbook(result["main_file"])
    labels = [row[0] for row in book["Summary"].iter_rows(values_only=True)]
    start = labels.index("Forecast identity")
    assert start < labels.index("Closing cash by month")             # at the top, before the figures
    summary = {row[0]: row[1] for row in book["Summary"].iter_rows(values_only=True) if row[0]}
    for label, text in identity.items():
        assert summary[label] == text
    notes = {row[0]: row[1] for row in book["Notes"].iter_rows(values_only=True) if row[0]}
    for label in ("Plugin version", "Run at", "Model settings", "Input", "Input fingerprint"):
        assert notes[label] == identity[label]
    assert notes["Model"] == "ets" and "statsmodels" in notes["Library versions"]


def test_identity_names_the_model_that_ran(example):
    identity = run(example, model="seasonal_naive")["forecast_identity"]
    assert identity["Model"] == "seasonal_naive" and "nothing is fitted" in identity["Model settings"]


def test_fingerprint_follows_the_content_not_the_file(example, tmp_path):
    from openpyxl import load_workbook
    before = loader.load_inputs(example).fingerprint
    assert len(before) == 64 and loader.load_inputs(example).fingerprint == before
    made = call(tools.cash_forecast_template, output_dir=str(tmp_path / "t"), format="xlsx")
    book = load_workbook(made["path"])
    # the same example as a workbook: same content, same fingerprint
    assert loader.load_inputs(made["path"]).fingerprint == before
    # saved again, and with an empty row added: the content has not changed
    book["known_events"].insert_rows(6)
    saved = tmp_path / "saved_again.xlsx"
    book.save(saved)
    assert loader.load_inputs(saved).fingerprint == before
    # one amount changed: a different fingerprint
    book["known_events"].cell(3, 3).value = -286501
    changed = tmp_path / "changed.xlsx"
    book.save(changed)
    assert loader.load_inputs(changed).fingerprint != before
    edit(example, "scenarios", lambda rows: rows[:2] + [rows[2][:2] + ["-9"] + rows[2][3:]] + rows[3:])
    assert loader.load_inputs(example).fingerprint != before


# --- 0.2.0: the second example, a hotel with seasonal cash ------------------------------------------
HOTEL = ROOT / "examples" / "synthetic_hotel"


@pytest.fixture
def hotel(tmp_path):
    folder = tmp_path / "hotel"
    shutil.copytree(HOTEL, folder)
    return folder


def test_hotel_example_files_match_the_generator():
    import runpy
    before = {p.name: p.read_bytes() for p in HOTEL.glob("*.csv")}
    runpy.run_path(str(ROOT / "examples" / "build_hotel_example.py"), run_name="__main__")
    assert {p.name: p.read_bytes() for p in HOTEL.glob("*.csv")} == before


def test_hotel_example_passes_the_check(hotel):
    checked = call(tools.cash_forecast_validate, input_path=str(hotel))
    assert checked["success"] and checked["ready_to_run"]
    assert checked["data_kind"] == "synthetic" and checked["currency"] == "EUR"
    assert checked["origin_month"] == "2026-10-01" and checked["opening_cash"] == "690000.00"
    assert checked["history_ties_opening_to_closing"] == "yes"
    assert checked["minimum_cash"] == "150000.00"
    assert len(checked["lines"]) == 26 and checked["shortest_forecast_history_months"] == 36
    assert sum(line["treatment"] == "forecast" for line in checked["lines"]) == 16
    assert checked["scenarios"] == ["group_cancels", "more_direct_bookings", "refurbishment_overrun",
                                    "weak_season", "weak_season_with_credit_line"]
    assert all(model["can_run"] for model in checked["models"].values())


def test_hotel_example_tells_the_intended_story(hotel):
    result = run(hotel)
    cases = result["scenarios"]
    base, weak, bridged = cases["base"], cases["weak_season"], cases["weak_season_with_credit_line"]
    # the base case gets through the winter above the minimum cash; the low point is at its end
    assert base["months_below_minimum_cash"] == [] and base["months_below_zero"] == []
    assert base["lowest_closing_cash"]["month"] in ("2027-02-01", "2027-03-01")
    # a weak season does not: below the minimum from February, overdrawn in April
    assert set(weak["months_below_minimum_cash"]) >= {"2027-02-01", "2027-03-01", "2027-04-01", "2027-05-01"}
    assert "2027-04-01" in weak["months_below_zero"]
    # the seasonal credit line carries the same weak season through, and costs its interest
    assert bridged["months_below_minimum_cash"] == [] and bridged["months_below_zero"] == []
    assert Decimal(bridged["final_closing_cash"]) == Decimal(weak["final_closing_cash"]) - Decimal("7800.00")
    # fixed-amount scenarios move the year end by exactly their amount
    assert Decimal(cases["group_cancels"]["final_closing_cash"]) == Decimal(base["final_closing_cash"]) - 45000
    assert Decimal(cases["refurbishment_overrun"]["final_closing_cash"]) == Decimal(base["final_closing_cash"]) - 60000
    assert Decimal(cases["more_direct_bookings"]["final_closing_cash"]) > Decimal(base["final_closing_cash"])
    # the refund confirmed after the forecast date is left out
    (left_out,) = result["left_out_inputs"]
    assert left_out["line_id"] == "other_receipts" and left_out["reason"] == "not_known_at_origin"


def test_hotel_example_ties_to_the_cent_in_every_scenario(hotel):
    result = run(hotel)
    assert len(result["scenarios"]) == 6
    for scenario in result["scenarios"]:
        balance = Decimal(result["opening_cash"])
        for row in audit_rows(result, scenario, "cash_bridge.csv"):
            net = sum(Decimal(row[s]) for s in ("operating", "investing", "financing"))
            assert Decimal(row["opening_cash"]) == balance and net == Decimal(row["net_cash_movement"])
            balance += net
            assert Decimal(row["closing_cash"]) == balance
        assert Decimal(result["scenarios"][scenario]["final_closing_cash"]) == balance


def test_hotel_receipts_stay_receipts_and_payments_stay_payments(hotel):
    """A model on a seasonal line must not turn a quiet month into money flowing the wrong way."""
    inputs = loader.load_inputs(hotel)
    signs = {line_id: 1 if sum(values.values()) > 0 else -1 for line_id, values in inputs.actuals.items()
             if any(values.values())}
    forecast_lines = {l.line_id for l in inputs.schema.lines if l.treatment == "forecast"}
    for row in audit_rows(run(hotel), "base"):
        if row["line_id"] in forecast_lines:
            assert Decimal(row["point"]) * signs[row["line_id"]] > 0, row


def test_hotel_template_can_be_asked_for(hotel, tmp_path):
    made = call(tools.cash_forecast_template, output_dir=str(tmp_path / "t"), format="xlsx", example="hotel")
    assert made["success"] and Path(made["path"]).name == "cash_forecast_example_hotel.xlsx"
    assert made["contains"] == "synthetic example hotel (made-up data)"
    assert made["examples_available"] == ["manufacturer", "hotel"]
    assert run(made["path"])["closing_cash_by_month"] == run(hotel)["closing_cash_by_month"]
    folder = call(tools.cash_forecast_template, output_dir=str(tmp_path / "c"), format="csv", example="hotel")
    assert Path(folder["path"]).name == "cash_forecast_example_hotel"
    assert (Path(folder["path"]) / "history.csv").read_bytes() == (HOTEL / "history.csv").read_bytes()


def test_manufacturer_is_still_the_default_example_and_an_unknown_one_is_refused(tmp_path):
    made = call(tools.cash_forecast_template, output_dir=str(tmp_path))
    assert Path(made["path"]).name == "cash_forecast_example.xlsx"
    assert made["contains"] == "synthetic example manufacturer (made-up data)"
    refused = call(tools.cash_forecast_template, output_dir=str(tmp_path / "x"), example="airline")
    assert refused["success"] is False and "manufacturer, hotel" in refused["error"]


# --- 0.2.1: scenario percentages are shown and read the way the user sees them ----------------------
def _hotel_workbook_with_percent_cells(tmp_path, number_format):
    """The hotel template with every scenario percentage stored as a fraction (-8 becomes -0.08)."""
    from openpyxl import load_workbook
    folder = tmp_path / number_format.replace("%", "pct").replace(".", "_").replace('"', "q")
    made = call(tools.cash_forecast_template, output_dir=str(folder), format="xlsx", example="hotel")
    book = load_workbook(made["path"])
    for row in book["scenarios"].iter_rows(min_row=3):
        if isinstance(row[2].value, (int, float)):
            row[2].value, row[2].number_format = row[2].value / 100, number_format
    book.save(made["path"])
    return made["path"]


def test_template_shows_percentages_with_a_percent_sign_and_keeps_the_numbers(hotel, tmp_path):
    from openpyxl import load_workbook
    made = call(tools.cash_forecast_template, output_dir=str(tmp_path / "t"), format="xlsx", example="hotel")
    sheet = load_workbook(made["path"])["scenarios"]
    assert sheet["C2"].value == "percent" and sheet["C3"].value == -8
    assert sheet["C3"].number_format == 'General"%"' and sheet["C300"].number_format == 'General"%"'
    assert sheet["D15"].value == 250000 and sheet["D15"].number_format == "General"
    result, from_csv = run(made["path"]), run(hotel)
    assert result["forecast_identity"]["Input fingerprint"] == from_csv["forecast_identity"]["Input fingerprint"]
    assert result["closing_cash_by_month"] == from_csv["closing_cash_by_month"]
    empty = call(tools.cash_forecast_template, output_dir=str(tmp_path / "e"), format="xlsx", with_example=False)
    assert load_workbook(empty["path"])["scenarios"]["C2"].number_format == 'General"%"'


@pytest.mark.parametrize("number_format", ["0%", "0.00%", "0.0%;[Red]-0.0%"])
def test_cell_formatted_as_a_percentage_is_read_as_the_percentage_it_shows(hotel, tmp_path, number_format):
    result, expected = run(_hotel_workbook_with_percent_cells(tmp_path, number_format)), run(hotel)
    assert result["closing_cash_by_month"] == expected["closing_cash_by_month"]
    assert result["forecast_identity"]["Input fingerprint"] == expected["forecast_identity"]["Input fingerprint"]
    assert not [w for w in result["warnings"] if "is read as" in w]


@pytest.mark.parametrize("number_format", ["General", 'General"%"', "0.00"])
def test_plain_fraction_is_read_as_typed_and_the_warning_says_so(hotel, tmp_path, number_format):
    result, as_shown = run(_hotel_workbook_with_percent_cells(tmp_path, number_format)), run(hotel)
    assert result["forecast_identity"]["Input fingerprint"] != as_shown["forecast_identity"]["Input fingerprint"]
    column = 2                                          # scenario, line_id, percent, ...
    edit(hotel, "scenarios", lambda rows: [rows[0]] + [                # the same fractions, typed into the CSV
        r[:column] + [repr(float(r[column]) / 100) if r[column] else r[column]] + r[column + 1:] for r in rows[1:]])
    as_typed = run(hotel)                               # compared within this run: fitted figures differ by machine
    assert result["closing_cash_by_month"] == as_typed["closing_cash_by_month"]
    assert result["forecast_identity"]["Input fingerprint"] == as_typed["forecast_identity"]["Input fingerprint"]
    assert result["closing_cash_by_month"] != as_shown["closing_cash_by_month"]
    told = [w for w in result["warnings"] if "is read as" in w]
    assert len(told) == 14 and told[0] == ("scenarios row 3: percent -0.08 is read as -0.08%, which is less than 1%. "
                                           "If you meant -8%, type -8.")


def test_percent_typed_as_text_with_a_sign_still_works(hotel):
    expected = run(hotel)                               # same run, same machine: no fixed fitted figure
    column = 2                                          # scenario, line_id, percent, ...
    edit(hotel, "scenarios", lambda rows: [rows[0]] + [
        r[:column] + [r[column] + "%" if r[column] else r[column]] + r[column + 1:] for r in rows[1:]])
    result = run(hotel)
    assert result["closing_cash_by_month"] == expected["closing_cash_by_month"]
    assert not [w for w in result["warnings"] if "is read as" in w]
