"""Write the forecast as one Excel workbook: a summary, then one cash-flow statement per scenario.

Each statement runs from opening cash to closing cash. Columns: the last actual
months (from the history the user supplied), then the 12 forecast months. Every
figure is the engine's; the workbook holds values, not formulas, so nothing can
drift from the reconciled result.
"""
from __future__ import annotations

from .engine.cash_flow_schema import SECTIONS

ACTUAL_MONTHS_SHOWN = 12
BASE = "base"
SECTION_TITLES = {"operating": "Operating activities", "investing": "Investing activities",
                  "financing": "Financing activities"}
NUMBER = '#,##0.00;[Red]-#,##0.00'
FORBIDDEN = set('[]:*?/\\')
RESERVED = {"summary", "how this is built", "where numbers come from", "notes"}
MODEL_IN_WORDS = {
    "ets": "ETS (exponential smoothing). It reads three things from your history, the current level, "
           "the trend and the pattern that repeats every year, and continues them.",
    "sarima": "SARIMA. It projects the line from its own past changes, including the change against "
              "the same month a year earlier.",
    "baseline": "Seasonal level plus trend. For each calendar month it takes the typical level and adds "
                "the average yearly change.",
    "seasonal_drift": "Same month last year, plus the average change from year to year.",
    "seasonal_naive": "Same month last year, unchanged.",
    "previous_month": "The last actual month, repeated. A sanity check, not a real forecast.",
    "chronos_2": "Chronos-2, a pre-trained forecasting model from Amazon. It continues the pattern it "
                 "recognises in your history.",
}
REASONS = {"not_known_at_origin": "only known after the forecast date",
           "not_available_at_origin": "only decided after the forecast date",
           "outside_horizon": "outside the 12 forecast months"}


def _label(month):
    return f"{month.year:04d}-{month.month:02d}"


def _title(name):
    return "Base case" if name == BASE else name


def _ranges(indexes, months):
    """[0, 1, 5] -> '2026-10 to 2026-11, 2027-03'."""
    groups = []
    for i in sorted(indexes):
        if groups and i == groups[-1][1] + 1:
            groups[-1][1] = i
        else:
            groups.append([i, i])
    return ", ".join(_label(months[a]) if a == b else f"{_label(months[a])} to {_label(months[b])}"
                     for a, b in groups)


def describe_treatments(run, replaced, continued=()):
    """Per line: the words for the Treatment column and the source of each forecast month.

    Sources: 'model', 'known' (a known line, or a known amount that replaced the model figure)
    and 'on_top' (model figure plus a known amount).
    """
    months = [m.month for m in run.statement.months]
    result = {}
    for position, line in enumerate(run.schema.lines):
        if line.treatment != "forecast":
            text = "known" if line.treatment == "known" else "scenario only"
            result[line.line_id] = (text, ["known"] * len(months))
            continue
        swapped = [i for i, m in enumerate(months) if (line.line_id, _label(m)) in replaced]
        on_top = [i for i, m in enumerate(run.statement.months)
                  if i not in swapped and m.lines[position].known_overlay]
        if not swapped:
            text = "forecast"
        elif len(swapped) == len(months):
            text = "known in every month"
        elif line.line_id in continued:
            text = f"known {_ranges(swapped, months)}, then forecast from that level"
        elif swapped == list(range(len(swapped))):
            text = f"known {_ranges(swapped, months)}, then forecast"
        else:
            text = f"forecast; known in {_ranges(swapped, months)}"
        if on_top:
            text += f", plus known on top {_ranges(on_top, months)}"
        sources = ["known" if i in swapped else "on_top" if i in on_top else "model" for i in range(len(months))]
        result[line.line_id] = (text, sources)
    return result


def _sheet_names(names):
    used, result = set(RESERVED), {}
    for name in names:
        clean = "".join("_" if c in FORBIDDEN else c for c in _title(name)).strip("'")[:28] or "scenario"
        candidate, count = clean, 2
        while candidate.lower() in used:
            candidate, count = f"{clean[:25]} ({count})", count + 1
        used.add(candidate.lower())
        result[name] = candidate
    return result


def left_out_items(runs, inputs):
    """Known amounts and scenario rows the forecast did not use, with the reason in words."""
    events = {e.event_id: e for e in inputs.known_events}
    legacy = {a.assumption_id: a for a in inputs.assumptions}
    labels = {line.line_id: line.label for line in runs[BASE].schema.lines}
    items = {}
    for run in runs.values():
        for decision in run.statement.excluded_inputs:
            if decision.reason == "scenario_not_selected" or decision.input_id in items:
                continue
            source = events.get(decision.input_id) or legacy.get(decision.input_id)
            known_from = getattr(source, "known_from_month", None) or getattr(source, "available_from_month", None)
            reason = REASONS.get(decision.reason, decision.reason)
            if known_from and decision.reason != "outside_horizon":
                reason += f" (known from {_label(known_from)})"
            items[decision.input_id] = {
                "line": labels.get(decision.line_id, decision.line_id),
                "month": _label(source.effective_month) if source else "",
                "amount": float(source.amount) if source else None,
                "reason": reason, "note": getattr(source, "note", "") if source else ""}
    return list(items.values())


def write_workbook(path, runs, inputs, library_versions=None):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    bold, white, italic = Font(bold=True), Font(bold=True, color="FFFFFF"), Font(italic=True)
    alert = Font(bold=True, color="C00000")
    head_fill = PatternFill("solid", fgColor="1F3864")
    actual_fill = PatternFill("solid", fgColor="EDEDED")
    total_fill = PatternFill("solid", fgColor="DDEBF7")
    cash_fill = PatternFill("solid", fgColor="FFF2CC")
    known_fill = PatternFill("solid", fgColor="E2EFDA")
    on_top_fill = PatternFill("solid", fgColor="FCE4D6")
    scenario_fill = PatternFill("solid", fgColor="E4DFEC")
    source_fill = {"known": known_fill, "on_top": on_top_fill, "model": None}
    right = Alignment(horizontal="right")
    wrap = Alignment(wrap_text=True, vertical="top")

    base = runs[BASE]
    schema = base.schema
    definitions = {line.line_id: line for line in schema.lines}
    currency = base.statement.currency
    used_models = sorted({a["used_model"] for a in base.model_audit})
    models = ", ".join(used_models) or "none"
    forecast_months = [m.month for m in base.statement.months]
    supplied = sorted({month for values in inputs.actuals.values() for month in values
                       if month <= inputs.origin_month})
    actual_months = supplied[-ACTUAL_MONTHS_SHOWN:]
    months = actual_months + forecast_months
    complete = all(inputs.actuals.get(line.line_id) for line in schema.lines if line.treatment != "assumption")
    replaced = {(item["line_id"], item["month"])
                for item in base.inputs.get("model_forecast_replaced_by_known_amount", [])}
    continued = {item["line_id"]: item for item in base.inputs.get("model_continued_from_known_amounts", [])}
    raw_forecasts = {f.line_id: f for f in base.inputs.get("base_forecasts", ())}
    names = _sheet_names(runs)
    treatments = describe_treatments(base, replaced, continued)
    left_out = left_out_items(runs, inputs)
    minimum = inputs.minimum_cash
    period = (f"Forecast for {_label(forecast_months[0])} to {_label(forecast_months[-1])}; "
              f"last actual month {_label(inputs.origin_month)}.")

    book = Workbook()

    def number_cell(sheet, row, column, value, *, font=None, fill=None):
        cell = sheet.cell(row, column, None if value is None else round(float(value), 2))
        cell.number_format = NUMBER
        if font:
            cell.font = font
        if fill:
            cell.fill = fill
        return cell

    def header(sheet, row, texts, start=1):
        for column, text in enumerate(texts, start=start):
            cell = sheet.cell(row, column, text)
            cell.font, cell.fill = white, head_fill
            if column > start:
                cell.alignment = right

    # --- Summary ------------------------------------------------------------------
    summary = book.active
    summary.title = "Summary"
    summary["A1"] = f"12-month cash forecast ({currency})"
    summary["A1"].font = Font(bold=True, size=14)
    summary["A2"] = (f"{period} Model: {models}. Data: {base.data_kind}. "
                     f"Opening cash {float(base.statement.opening_cash):,.2f}.")
    row = 4
    header(summary, row, ["Closing cash by month"] + [_title(name) for name in runs])
    for index, month in enumerate(forecast_months):
        row += 1
        summary.cell(row, 1, _label(month))
        for column, run in enumerate(runs.values(), start=2):
            closing = run.statement.months[index].closing_cash
            low = closing < 0 or (minimum is not None and float(closing) < minimum)
            number_cell(summary, row, column, closing, font=alert if low else None)
    row += 2
    for label, value in (("Final closing cash", lambda run: run.statement.months[-1].closing_cash),
                         ("Lowest closing cash", lambda run: min(m.closing_cash for m in run.statement.months))):
        summary.cell(row, 1, label).font = bold
        summary.cell(row, 1).fill = cash_fill
        for column, run in enumerate(runs.values(), start=2):
            number_cell(summary, row, column, value(run), font=bold, fill=cash_fill)
        row += 1

    def text_row(label, value):
        nonlocal row
        summary.cell(row, 1, label).font = bold
        for column, run in enumerate(runs.values(), start=2):
            text, flagged = value(run)
            cell = summary.cell(row, column, text)
            cell.alignment = Alignment(horizontal="right", wrap_text=True, vertical="top")
            if flagged:
                cell.font = alert
        row += 1

    def below(limit):
        def value(run):
            hit = [m.month for m in run.statement.months if float(m.closing_cash) < limit]
            indexes = [forecast_months.index(m) for m in hit]
            return (_ranges(indexes, forecast_months) or "none", bool(hit))
        return value

    text_row("Month of lowest closing cash",
             lambda run: (_label(min(run.statement.months, key=lambda m: m.closing_cash).month), False))
    text_row("Months with closing cash below zero", below(0))
    if minimum is not None:
        text_row(f"Months below your minimum cash of {minimum:,.2f}", below(minimum))
    summary.cell(row, 1, "Difference to base case, final closing cash").font = bold
    for column, run in enumerate(runs.values(), start=2):
        number_cell(summary, row, column,
                    run.statement.months[-1].closing_cash - base.statement.months[-1].closing_cash)
    row += 3

    summary.cell(row, 1, "Left out of the forecast").font = bold
    row += 1
    if left_out:
        header(summary, row, ["Line", "Month", "Amount", "Why it is left out", "Your note"])
        for item in left_out:
            row += 1
            summary.cell(row, 1, item["line"])
            summary.cell(row, 2, item["month"]).alignment = right
            number_cell(summary, row, 3, item["amount"], font=alert)
            summary.cell(row, 4, item["reason"])
            summary.cell(row, 5, item["note"])
        row += 1
        summary.cell(row, 1, "These amounts are in NO scenario above unless a scenario adds them. A known amount "
                             "counts only if you knew it by the last actual month.").font = italic
    else:
        summary.cell(row, 1, "Nothing. Every known amount in the next 12 months is included.").font = italic
    row += 3

    scenario_names = [name for name in runs if name != BASE]
    if scenario_names:
        summary.cell(row, 1, "What each scenario changes").font = bold
        row += 1
        header(summary, row, ["Scenario", "Line", "Change", "Months", "Effect over 12 months", "Your note"])
        for name in scenario_names:
            effect = {}
            for month in runs[name].statement.months:
                for line in month.lines:
                    if line.assumption_overlay:
                        effect[line.line_id] = effect.get(line.line_id, 0) + line.assumption_overlay
            rules = [r for r in inputs.rules if r.scenario == name]
            for rule in rules:
                row += 1
                once = rule.from_month and rule.from_month == rule.to_month
                if rule.percent is not None:
                    change = f"{rule.percent:+g}%"
                else:
                    change = f"{rule.amount:+,.2f} " + ("once" if once else "per month")
                if once:
                    when = _label(rule.from_month)
                elif rule.from_month or rule.to_month:
                    when = (f"{_label(rule.from_month) if rule.from_month else 'start'} to "
                            f"{_label(rule.to_month) if rule.to_month else 'end'}")
                else:
                    when = "all 12"
                for column, value in enumerate((name, definitions[rule.line_id].label, change, when), start=1):
                    summary.cell(row, column, value)
                if len([r for r in rules if r.line_id == rule.line_id]) == 1:
                    number_cell(summary, row, 5, effect.get(rule.line_id, 0))
                summary.cell(row, 6, rule.note)
            for line_id in sorted({a.line_id for a in inputs.assumptions if a.scenario_id == name}):
                row += 1
                for column, value in enumerate((name, definitions[line_id].label, "amounts per month"), start=1):
                    summary.cell(row, column, value)
                number_cell(summary, row, 5, effect.get(line_id, 0))
            row += 1
            summary.cell(row, 1, name).font = bold
            summary.cell(row, 2, "Total effect on final closing cash").font = bold
            number_cell(summary, row, 5, sum(effect.values()), font=bold, fill=total_fill)
            row += 1
        row += 1
        summary.cell(row, 1, "Percentages are taken from each line's base-case figure for that month. "
                             "The base case contains no scenario.").font = italic
    summary.column_dimensions["A"].width = 46
    for column, width in zip("BCDEFGH", (40, 20, 34, 24, 46, 20, 20)):
        summary.column_dimensions[column].width = width
    if len(runs) > 4:
        for column in range(6, 2 + len(runs)):
            summary.column_dimensions[get_column_letter(column)].width = 20

    # --- How this is built ----------------------------------------------------------
    built = book.create_sheet("How this is built")
    forecast_lines = [line for line in schema.lines if line.treatment == "forecast"]
    known_lines = [line for line in schema.lines if line.treatment != "forecast"]
    history_months = min((item["train_months"] for item in base.model_audit), default=0)
    swapped_lines = sorted({definitions[line_id].label for line_id, _ in replaced})
    on_top_lines = sorted(definitions[line_id].label for line_id, (_, sources) in treatments.items()
                          if "on_top" in sources)
    steps = [
        ("How this forecast is built", ""),
        ("In one sentence", "Each line of your cash-flow statement is either projected by a statistical model "
                            "or taken from the amounts you already know; the lines are added up month by month "
                            "from opening cash to closing cash; scenarios are then layered on top."),
        ("1. Starting point", f"Opening cash is {float(base.statement.opening_cash):,.2f} {currency}: the closing "
                              f"cash of {_label(inputs.origin_month)}, your last actual month "
                              f"(taken from the {inputs.opening_cash_source})."),
        ("2. Lines projected by the model",
         f"{len(forecast_lines)} line(s) are projected from {history_months} months of your history. "
         + " ".join(MODEL_IN_WORDS.get(name, name) for name in used_models)
         + " The model sees only the history of that one line; it knows nothing about your plans."),
        ("3. Lines you already know",
         f"{len(known_lines)} line(s) use only the amounts you entered in known_events, for example rent, tax "
         "and loan repayments. A month without an amount is zero. Nothing is predicted for these lines."),
        ("4. Known amounts on a projected line",
         ("Replace: " + ", ".join(swapped_lines) + ". For the months you entered, your amount is used and the "
          "model's figure is not. " if swapped_lines else "No known amount replaces a model figure. ")
         + ("Where your amounts cover the first months, the later months keep the model's pattern but start "
            "from the level of your last amount, so the line does not fall back. " if continued else "")
         + ("On top: " + ", ".join(on_top_lines) + ". Your amount is added to the model's figure, for "
            "something new that the history does not contain." if on_top_lines else "")),
        ("5. What is left out",
         "A known amount counts only if you knew it by the last actual month. Amounts learned later, or "
         f"outside the 12 months, are left out and listed on the Summary sheet ({len(left_out)} item(s) here)."),
        ("6. Scenarios",
         "The base case contains no scenario. Each scenario is the base case plus its own changes. A percentage "
         "is taken from the base-case figure of that line and month: -4% on receipts means 4% less cash in, "
         "+4% on a payment line means 4% more cash out. The model is not run again for a scenario."),
        ("7. The check",
         "For every month and every scenario: operating + investing + financing = net cash flow; opening cash "
         "+ net cash flow = closing cash; closing cash = next month's opening cash. This is checked to the "
         "cent. If it did not tie, no result would have been written."),
        ("8. Who calculated it",
         "Every figure was calculated by the forecasting engine in code. The AI assistant chose the steps and "
         "wrote the explanation; it did not calculate or adjust any number."),
        ("9. What this does not tell you",
         "There is no range around closing cash, only one figure per month. A forecast is not a promise: it "
         "continues the past and adds what you know. Compare models (ask for a model comparison) and review "
         "the scenarios before relying on it."),
        ("Colours on the scenario sheets",
         "No shading = projected by the model. Green = your known amount. Orange = model plus your amount on "
         "top. Purple = changed by the scenario. Grey = your actuals."),
    ]
    for index, (title, text) in enumerate(steps, start=1):
        built.cell(index, 1, title).font = Font(bold=True, size=14) if index == 1 else bold
        built.cell(index, 1).alignment = wrap
        built.cell(index, 2, text).alignment = wrap
    built.column_dimensions["A"].width = 34
    built.column_dimensions["B"].width = 120

    # --- one statement per scenario -------------------------------------------------
    def actual(line_id, month):
        return inputs.actuals.get(line_id, {}).get(month)

    def write_statement(sheet, name, run):
        by_month = {m.month: m for m in run.statement.months}
        lines = run.statement.months[0].lines
        sheet["A1"] = f"12-month cash forecast ({currency}): {_title(name)}"
        sheet["A1"].font = Font(bold=True, size=14)
        sheet["A2"] = (f"{period} Model: {models}. Data: {run.data_kind}. "
                       "Opening cash + net cash flow = closing cash, checked to the cent.")
        header_row, kind_row = 4, 5
        header(sheet, header_row, ["Cash-flow line", "Treatment"] + [_label(m) for m in months])
        sheet.cell(header_row, 2).alignment = Alignment(horizontal="left")
        for offset, month in enumerate(months):
            kind = sheet.cell(kind_row, 3 + offset, "Actual" if month in actual_months else "Forecast")
            kind.alignment, kind.font = right, italic

        def put(row, label, values, *, font=None, fill=None, treatment="", position=None, flag_low=False):
            sheet.cell(row, 1, label).font = font or Font()
            sheet.cell(row, 2, treatment)
            line_id = lines[position].line_id if position is not None else None
            for offset, value in enumerate(values):
                month = months[offset]
                shade = fill or (actual_fill if month in actual_months else None)
                cell_font = font
                if line_id and month in by_month:
                    shade = source_fill[treatments[line_id][1][offset - len(actual_months)]]
                    if by_month[month].lines[position].assumption_overlay:
                        shade = scenario_fill
                if flag_low and value is not None and month in by_month:
                    if value < 0 or (minimum is not None and float(value) < minimum):
                        cell_font = alert
                number_cell(sheet, row, 3 + offset, value, font=cell_font, fill=shade)
            if fill:
                sheet.cell(row, 1).fill = fill
                sheet.cell(row, 2).fill = fill

        def section_total(section, month):
            if month in by_month:
                return by_month[month].sections[section]
            if not complete:
                return None
            return round(sum(actual(line.line_id, month) or 0.0
                             for line in schema.lines if line.section == section), 2)

        def net(month):
            if month in by_month:
                return by_month[month].net_cash_movement
            totals = [section_total(section, month) for section in SECTIONS]
            return None if None in totals else round(sum(totals), 2)

        row = 6
        put(row, "Opening cash", [by_month[m].opening_cash if m in by_month
                                  else inputs.balances["opening_cash"].get(m) for m in months],
            font=bold, fill=cash_fill)
        row += 2
        for section in SECTIONS:
            sheet.cell(row, 1, SECTION_TITLES[section]).font = bold
            row += 1
            for position, line in enumerate(lines):
                definition = definitions[line.line_id]
                if definition.section != section:
                    continue
                values = [by_month[m].lines[position].point if m in by_month else actual(line.line_id, m)
                          for m in months]
                put(row, "  " + definition.label, values, treatment=treatments[line.line_id][0],
                    position=position)
                row += 1
            put(row, f"Net cash from {section} activities", [section_total(section, m) for m in months],
                font=bold, fill=total_fill)
            row += 2
        put(row, "Net cash flow", [net(m) for m in months], font=bold, fill=total_fill)
        row += 1
        put(row, "Closing cash", [by_month[m].closing_cash if m in by_month
                                  else inputs.balances["closing_cash"].get(m) for m in months],
            font=bold, fill=cash_fill, flag_low=True)
        row += 2
        sheet.cell(row, 1, "Where each forecast figure comes from").font = bold
        legend = [(None, "No shading: projected by the model"),
                  (known_fill, "Your known amount (a known line, or it replaced the model's figure)"),
                  (on_top_fill, "The model's figure plus your known amount on top")]
        if name != BASE:
            legend.append((scenario_fill, "Changed by this scenario"))
        legend.append((actual_fill, "Your actuals"))
        for fill, text in legend:
            row += 1
            if fill:
                sheet.cell(row, 1).fill = fill
            sheet.cell(row, 2, text).font = italic
        row += 1
        low_text = "Closing cash in red: below zero" + (f" or below your minimum cash of {minimum:,.2f}"
                                                        if minimum is not None else "")
        sheet.cell(row, 2, low_text).font = italic
        sheet.column_dimensions["A"].width = 48
        sheet.column_dimensions["B"].width = 50
        for offset in range(len(months)):
            sheet.column_dimensions[get_column_letter(3 + offset)].width = 16
        sheet.freeze_panes = sheet.cell(6, 3)

    for name, run in runs.items():
        write_statement(book.create_sheet(names[name]), name, run)

    # --- where each forecast figure comes from ---------------------------------------
    detail = book.create_sheet("Where numbers come from")
    detail.append(["Scenario", "Month", "Line", "Source", "Model figure used", "Known amounts",
                   "Scenario change", "Total", "Model", "Model figure not used", "Note"])
    for cell in detail[1]:
        cell.font = bold
    for name, run in runs.items():
        for month in run.statement.months:
            for line in month.lines:
                if not (line.base_point or line.known_overlay or line.assumption_overlay):
                    continue
                index = forecast_months.index(month.month)
                source = {"known": "known amount", "on_top": "model + known amount on top",
                          "model": "model"}[treatments[line.line_id][1][index]]
                unused, note = None, ""
                if (line.line_id, _label(month.month)) in replaced:
                    raw = raw_forecasts.get(line.line_id)
                    unused = round(float(raw.point[index]), 2) if raw else None
                    note = "your known amount replaced the model's figure"
                elif line.line_id in continued:
                    shift = float(continued[line.line_id]["level_shift"])
                    note = f"model's pattern, moved by {shift:+,.2f} to start from your last known amount"
                detail.append([_title(name), _label(month.month), definitions[line.line_id].label, source,
                               float(line.base_point), float(line.known_overlay),
                               float(line.assumption_overlay), float(line.point),
                               line.model_name or "", unused, note])
    for column, width in zip("ABCDEFGHIJK", (16, 10, 46, 30, 18, 16, 16, 16, 12, 22, 70)):
        detail.column_dimensions[column].width = width
    for cells in detail.iter_rows(min_row=2, min_col=5, max_col=10):
        for cell in cells:
            if cell.column != 9:
                cell.number_format = NUMBER
    detail.freeze_panes = "A2"
    detail.auto_filter.ref = detail.dimensions

    # --- notes ------------------------------------------------------------------------
    notes = book.create_sheet("Notes")
    rows = [["Model", models], ["Data kind (declared by the user)", base.data_kind],
            ["Last month of actuals", _label(inputs.origin_month)],
            ["Forecast months", f"{_label(forecast_months[0])} to {_label(forecast_months[-1])}"],
            ["Opening cash taken from", inputs.opening_cash_source],
            ["History ties from opening to closing cash",
             {True: "yes", False: "NO, see warnings", None: "not checked"}[inputs.history_ties]],
            ["Minimum cash", "not set" if minimum is None else f"{minimum:,.2f}"],
            ["Library versions", ", ".join(f"{k} {v}" for k, v in (library_versions or {}).items()) or "not recorded"],
            ["Scenarios in this workbook", ", ".join(_title(n) for n in runs)], [""],
            ["Left out of the forecast"]]
    rows += [[f"{item['line']}, {item['month']}, {item['amount']:,.2f}" if item["amount"] is not None
              else item["line"], item["reason"]] for item in left_out] or [["nothing"]]
    rows += [[""], ["Warnings"]] + ([[w] for w in inputs.warnings] or [["none"]])
    rows += [[""], ["Limits"],
             ["ETS and SARIMA are fitted numerically. On another machine or with other library versions the "
              "figures can differ by small amounts; the simple models give identical figures everywhere."],
             ["There is no uncertainty range for closing cash."],
             ["A forecast is not evidence of accuracy. Compare models and review the scenarios."]]
    for values in rows:
        notes.append(values)
    notes.column_dimensions["A"].width = 60
    notes.column_dimensions["B"].width = 60
    book.save(path)
    return path
