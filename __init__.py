"""Treasury Cash Forecast: a 12-month monthly cash-flow forecast for Hermes Agent.

Four tools in the ``treasury_cash_forecast`` toolset and one bundled skill. The
arithmetic and the cash reconciliation run in a tested engine (see ``engine/``);
the language model only chooses the tool and explains the result.

The plugin makes no network calls, needs no credentials, and only reads the
input the user points to and writes output files next to it (or where asked).
"""
from __future__ import annotations

from pathlib import Path

from . import schemas, tools

TOOLSET = "treasury_cash_forecast"
SKILL = "cash-forecast-mapping"
SKILL_DESCRIPTION = ("Map a company's own cash-flow spreadsheet into the Treasury Cash Forecast "
                     "template, confirm the mapping with the user, then validate and run.")

_TOOLS = (
    (schemas.CASH_FORECAST_TEMPLATE, tools.cash_forecast_template, "📄"),
    (schemas.CASH_FORECAST_VALIDATE, tools.cash_forecast_validate, "✅"),
    (schemas.CASH_FORECAST_RUN, tools.cash_forecast_run, "💶"),
    (schemas.CASH_FORECAST_COMPARE, tools.cash_forecast_compare, "⚖️"),
)


def register(ctx) -> None:
    """Plugin entry point, called once by the Hermes plugin loader."""
    for schema, handler, emoji in _TOOLS:
        ctx.register_tool(name=schema["name"], toolset=TOOLSET, schema=schema,
                          handler=handler, emoji=emoji)
    ctx.register_skill(
        name=SKILL, path=Path(__file__).resolve().parent / "skills" / SKILL / "SKILL.md",
        description=SKILL_DESCRIPTION,
    )


__all__ = ["register"]
