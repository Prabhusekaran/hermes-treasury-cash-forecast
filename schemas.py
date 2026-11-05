"""Tool schemas: what the language model reads to decide when and how to call each tool."""

_INPUT_PATH = {
    "type": "string",
    "description": "Path to the filled template: one .xlsx workbook, or a folder holding "
                   "inputs.csv, history.csv, known_events.csv and scenarios.csv.",
}
_MODEL_NAMES = ["ets", "sarima", "baseline", "seasonal_drift", "seasonal_naive",
                "previous_month", "chronos_2"]

CASH_FORECAST_TEMPLATE = {
    "name": "cash_forecast_template",
    "description": (
        "Create the input template for a 12-month corporate cash-flow forecast, either filled with a "
        "made-up example company or empty. Use this first when the user has no template yet, or wants "
        "to see what the forecast needs. Returns the path of the file or folder to fill in."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "output_dir": {"type": "string", "description": "Folder where the template is saved."},
            "format": {"type": "string", "enum": ["xlsx", "csv"],
                       "description": "xlsx = one Excel workbook (default); csv = a folder of CSV files."},
            "with_example": {"type": "boolean",
                             "description": "true (default) = include the synthetic example company; "
                                            "false = empty template with headings only."},
        },
        "required": ["output_dir"],
    },
}

CASH_FORECAST_VALIDATE = {
    "name": "cash_forecast_validate",
    "description": (
        "Check a filled cash-forecast template before running it. Reports every problem in plain "
        "language (missing months, unknown lines, wrong section or treatment), whether the history "
        "ties from opening cash to closing cash every month, the opening cash the forecast will "
        "start from, how many months of history each forecast line has, the known amounts and "
        "scenarios found for each line (so the user can confirm they all still apply), which models "
        "can run, and treasury warnings such as possible double counting. Always call this before cash_forecast_run and "
        "show the user the problems or warnings. It does not forecast anything."
    ),
    "parameters": {"type": "object", "properties": {"input_path": _INPUT_PATH}, "required": ["input_path"]},
}

CASH_FORECAST_RUN = {
    "name": "cash_forecast_run",
    "description": (
        "Run the 12-month monthly direct cash-flow forecast from a filled template. Forecast lines are "
        "projected by the chosen statistical model, known lines come from the supplied schedule, and a "
        "known amount on a forecast line replaces or adds to the model figure as the user marked it. "
        "The base case and every scenario in the template (percentages of a line, or fixed amounts) "
        "are run together. Opening cash is the closing cash of the last actual month. The engine "
        "reconciles opening cash to closing cash to the cent for every month and stops if anything "
        "does not tie. Returns, per scenario, final closing cash, the lowest-cash month, any months "
        "below zero and the effect per line; closing cash by month side by side; what was left out "
        "and why; and the output: cash_forecast.xlsx (a summary plus one cash-flow statement per "
        "scenario, actuals then forecast) and an audit folder per scenario. "
        "Report the returned numbers exactly; never recalculate or estimate cash figures yourself."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "input_path": _INPUT_PATH,
            "model": {"type": "string", "enum": _MODEL_NAMES,
                      "description": "Forecasting model for the forecast lines. Default 'ets' (needs 24 months "
                                     "of history). 'chronos_2' is optional and must already be installed."},
            "scenarios": {"type": "array", "items": {"type": "string"},
                          "description": "Scenario names to run next to the base case. Leave out to run "
                                         "every scenario in the template; pass an empty list for the "
                                         "base case only."},
            "output_dir": {"type": "string",
                           "description": "Where to save the output files. Default: a "
                                          "'cash_forecast_output' folder next to the input."},
        },
        "required": ["input_path"],
    },
}

CASH_FORECAST_COMPARE = {
    "name": "cash_forecast_compare",
    "description": (
        "Run the same cash forecast with several models and compare final closing cash, lowest cash "
        "and months below zero side by side. Use it when the user asks how sensitive the forecast is "
        "to the model, or which model to trust. It writes no files and is not an accuracy test."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "input_path": _INPUT_PATH,
            "models": {"type": "array", "items": {"type": "string", "enum": _MODEL_NAMES},
                       "description": "Models to compare. Default: all six built-in statistical models."},
            "scenario": {"type": "string", "description": "Optional scenario to compare the models on. "
                                                          "Leave empty for the base case."},
        },
        "required": ["input_path"],
    },
}
