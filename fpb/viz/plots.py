"""Plotly figures for the dashboard.

Each builder takes a simulation frame already in the chosen view (nominal or
real) and the plan. Eras are drawn as shaded bands on every chart so the
time series can be read against the life stages they belong to.
"""

from __future__ import annotations

from datetime import date

import plotly.graph_objects as go
import polars as pl

from fpb.models import Plan
from fpb.viz.theme import ERA_BAND_OPACITY, LAYOUT, era_colors, series_colors


def _x(df: pl.DataFrame) -> list[date]:
    """Month strings as first-of-month dates so Plotly treats the axis as time."""
    return df.select((pl.col("month") + "-01").str.to_date("%Y-%m-%d")).to_series().to_list()


def _add_era_bands(fig: go.Figure, plan: Plan, df: pl.DataFrame) -> None:
    colors = era_colors(plan)
    last = df["month"][-1]
    for era in plan.eras:
        lo = era.start
        hi = era.end if era.end is not None else last
        if hi < lo or lo > last:
            continue
        fig.add_vrect(
            x0=date.fromisoformat(lo + "-01"),
            x1=date.fromisoformat(min(hi, last) + "-01"),
            fillcolor=colors[era.id],
            opacity=ERA_BAND_OPACITY,
            layer="below",
            line_width=0,
            annotation_text=era.label,
            annotation_position="top left",
            annotation_font_size=10,
        )


def _base_fig(title: str, y_title: str, plan: Plan, df: pl.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(title=title, yaxis_title=y_title, **LAYOUT)
    fig.update_yaxes(tickformat=",.0f", rangemode="tozero")
    _add_era_bands(fig, plan, df)
    return fig


def _cols(df: pl.DataFrame, prefix: str) -> list[str]:
    return [c for c in df.columns if c.startswith(prefix)]


def net_worth(df: pl.DataFrame, plan: Plan, real: bool) -> go.Figure:
    """Net worth line with its components: cash, assets, and debts (as negative)."""
    x = _x(df)
    unit = " (real)" if real else ""
    fig = _base_fig(f"Net worth{unit}", "USD", plan, df)
    fig.update_yaxes(rangemode="normal")
    fig.add_trace(go.Scatter(x=x, y=df["cash"].to_list(), name="Cash", stackgroup="nw_pos",
                             line=dict(width=0), fillcolor="rgba(84,162,75,0.5)"))
    fig.add_trace(go.Scatter(x=x, y=df["assets_total"].to_list(), name="Assets", stackgroup="nw_pos",
                             line=dict(width=0), fillcolor="rgba(76,120,168,0.5)"))
    fig.add_trace(go.Scatter(x=x, y=(-df["debts_total"]).to_list(), name="Debts (negative)",
                             stackgroup="nw_neg", line=dict(width=0), fillcolor="rgba(228,87,86,0.5)"))
    fig.add_trace(go.Scatter(x=x, y=df["net_worth"].to_list(), name="Net worth",
                             mode="lines", line=dict(color="black", width=3)))
    return fig


def debts(df: pl.DataFrame, plan: Plan, real: bool) -> go.Figure:
    """Stacked balance per debt account, with total interest paid per month."""
    x = _x(df)
    unit = " (real)" if real else ""
    fig = _base_fig(f"Debt balances{unit}", "USD", plan, df)
    names = _cols(df, "debt__")
    colors = series_colors(names)
    for c in names:
        fig.add_trace(go.Scatter(
            x=x, y=df[c].to_list(), name=c.removeprefix("debt__"), stackgroup="debt",
            line=dict(width=0), fillcolor=_rgba(colors[c], 0.6),
        ))
    fig.add_trace(go.Scatter(x=x, y=df["interest"].to_list(), name="Interest this month",
                             mode="lines", yaxis="y2", line=dict(color="#E45756", dash="dot")))
    fig.update_layout(yaxis2=dict(title="Interest / month", overlaying="y", side="right",
                                  tickformat=",.0f", showgrid=False))
    return fig


def expenses(df: pl.DataFrame, plan: Plan, real: bool) -> go.Figure:
    """Stacked monthly expenses by category, with income for context."""
    x = _x(df)
    unit = " (real)" if real else ""
    fig = _base_fig(f"Monthly expenses by category{unit}", "USD / month", plan, df)
    names = _cols(df, "exp__")
    colors = series_colors(names)
    for c in names:
        fig.add_trace(go.Scatter(
            x=x, y=df[c].to_list(), name=c.removeprefix("exp__"), stackgroup="exp",
            line=dict(width=0), fillcolor=_rgba(colors[c], 0.7),
        ))
    fig.add_trace(go.Scatter(x=x, y=df["income_net"].to_list(), name="Net income",
                             mode="lines", line=dict(color="black", dash="dash")))
    return fig


def cash_flow(df: pl.DataFrame, plan: Plan, real: bool) -> go.Figure:
    """Where net income goes each month, and the resulting savings rate."""
    x = _x(df)
    unit = " (real)" if real else ""
    fig = _base_fig(f"Cash flow{unit}", "USD / month", plan, df)
    series = [
        ("Expenses", "expenses", "#F58518"),
        ("Debt service (minimum)", "minimum_payments", "#E45756"),
        ("Extra debt payments", "extra_debt_payments", "#B279A2"),
        ("Recurring contributions", "recurring_contributions", "#54A24B"),
    ]
    for label, col, color in series:
        fig.add_trace(go.Bar(x=x, y=df[col].to_list(), name=label, marker_color=color))
    fig.add_trace(go.Scatter(x=x, y=df["income_net"].to_list(), name="Net income",
                             mode="lines", line=dict(color="black", width=2)))
    fig.update_layout(barmode="stack")
    return fig


def assets(df: pl.DataFrame, plan: Plan, real: bool) -> go.Figure:
    """Stacked balance per asset account, with cash shown separately."""
    x = _x(df)
    unit = " (real)" if real else ""
    fig = _base_fig(f"Asset balances{unit}", "USD", plan, df)
    names = _cols(df, "asset__")
    colors = series_colors(names)
    for c in names:
        fig.add_trace(go.Scatter(
            x=x, y=df[c].to_list(), name=c.removeprefix("asset__"), stackgroup="assets",
            line=dict(width=0), fillcolor=_rgba(colors[c], 0.7),
        ))
    return fig


def deficit_strip(df: pl.DataFrame, plan: Plan, real: bool) -> go.Figure:
    """Cash balance over time; any month below zero is a funding gap."""
    x = _x(df)
    unit = " (real)" if real else ""
    fig = _base_fig(f"Cash balance{unit}", "USD", plan, df)
    fig.update_yaxes(rangemode="normal")
    fig.add_trace(go.Scatter(x=x, y=df["cash"].to_list(), name="Cash", mode="lines",
                             line=dict(color="#54A24B", width=2)))
    fig.add_hline(y=0, line_color="#E45756", line_dash="dot")
    return fig


def dashboard(df: pl.DataFrame, plan: Plan, real: bool) -> dict[str, go.Figure]:
    return {
        "Net worth": net_worth(df, plan, real),
        "Debts": debts(df, plan, real),
        "Expenses": expenses(df, plan, real),
        "Cash flow": cash_flow(df, plan, real),
        "Assets": assets(df, plan, real),
        "Cash": deficit_strip(df, plan, real),
    }


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"
