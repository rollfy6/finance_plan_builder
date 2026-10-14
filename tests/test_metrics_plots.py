from pathlib import Path

import plotly.graph_objects as go
import polars as pl
import pytest

from fpb.deflator import view
from fpb.engine import simulate
from fpb.metrics import annual, summary
from fpb.storage import load_plan
from fpb.viz import plots
from tests.helpers import NoTax, build

EXAMPLE = Path(__file__).resolve().parent.parent / "plans" / "example.yaml"


@pytest.fixture(scope="module")
def example():
    plan = load_plan(EXAMPLE)
    df = simulate(plan, horizon_end="2045-12")
    return plan, df


# --- metrics ---------------------------------------------------------------

def test_summary_matches_last_row(example):
    plan, df = example
    s = summary(df, plan)
    assert s["horizon_end"] == "2045-12"
    assert s["net_worth_end"] == pytest.approx(df["net_worth"][-1])


def test_debt_free_month_is_first_month_with_zero_debt(example):
    plan, df = example
    s = summary(df, plan)
    free = s["debt_free_month"]
    assert free is not None
    row = df.filter(pl.col("month") == free)
    assert row["debts_total"][0] <= 0.005
    prev = df.filter(pl.col("month") < free)
    assert (prev["debts_total"] > 0.005).any()


def test_debt_free_none_when_debt_never_cleared():
    p = build(end="2026-03", debts=[
        {"id": "m", "label": "m", "opening_balance": "100000", "apr": "0.05", "minimum_payment": "100"}])
    df = simulate(p, tax_model=NoTax())
    assert summary(df, p)["debt_free_month"] is None


def test_first_deficit_and_min_cash_reported():
    p = build(end="2026-03", expenses=[
        {"id": "x", "label": "x", "category": "c", "monthly": "500", "inflation_linked": False}])
    df = simulate(p, tax_model=NoTax())
    s = summary(df, p)
    assert s["first_deficit_month"] == "2026-01"
    assert s["deficit_months"] == 3
    assert s["min_cash"] == -1500.0


def test_annual_rollup_sums_flows_and_takes_year_end_balances(example):
    plan, df = example
    a = annual(df)
    y2027 = a.filter(pl.col("year") == "2027")
    raw = df.filter(pl.col("month").str.starts_with("2027"))
    assert y2027["income_net"][0] == pytest.approx(raw["income_net"].sum())
    assert y2027["net_worth_end"][0] == pytest.approx(raw["net_worth"][-1])


def test_savings_rate_is_one_minus_expense_ratio():
    p = build(end="2026-12", incomes=[{"id": "s", "label": "s", "gross_annual": "12000"}],
                   expenses=[{"id": "x", "label": "x", "category": "c", "monthly": "600",
                              "inflation_linked": False}])
    a = annual(simulate(p, tax_model=NoTax()))
    # 12000/yr gross = 1000/mo; spending 600/mo -> (1000-600)/1000 = 0.4
    assert a["savings_rate"][0] == pytest.approx(0.4)


def test_real_view_summary_uses_deflated_values(example):
    plan, df = example
    nominal = summary(df, plan)["net_worth_end"]
    real = summary(view(df, real=True), plan)["net_worth_end"]
    assert real < nominal


# --- figures ---------------------------------------------------------------

def test_dashboard_builds_all_figures(example):
    plan, df = example
    figs = plots.dashboard(df, plan, real=False)
    assert set(figs) == {"Net worth", "Debts", "Expenses", "Cash flow", "Assets", "Cash"}
    assert all(isinstance(f, go.Figure) for f in figs.values())


def test_era_bands_cover_each_era(example):
    plan, df = example
    fig = plots.net_worth(df, plan, real=False)
    bands = [s for s in fig.layout.shapes if s.type == "rect"]
    assert len(bands) == len(plan.eras)


def test_debt_traces_match_debt_columns(example):
    plan, df = example
    fig = plots.debts(df, plan, real=False)
    names = {t.name for t in fig.data}
    debt_cols = {c.removeprefix("debt__") for c in df.columns if c.startswith("debt__")}
    assert debt_cols <= names


def test_net_worth_trace_equals_frame(example):
    plan, df = example
    fig = plots.net_worth(df, plan, real=False)
    nw = next(t for t in fig.data if t.name == "Net worth")
    assert list(nw.y) == pytest.approx(df["net_worth"].to_list())


def test_real_title_is_marked(example):
    plan, df = example
    assert "(real)" in plots.net_worth(view(df, True), plan, real=True).layout.title.text


def test_time_axis_is_dates(example):
    plan, df = example
    fig = plots.cash_flow(df, plan, real=False)
    assert fig.data[0].x[0].isoformat() == "2023-06-01"
