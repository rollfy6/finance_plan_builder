"""Summary metrics and annual roll-ups computed from a simulation frame.

Every function takes the frame already in the desired view (nominal or real),
so the same code serves both toggles.
"""

from __future__ import annotations

from typing import Optional

import polars as pl

from fpb.models import Plan

_DEBT_EPS = 0.005  # half a cent: below this a balance is treated as paid off


def _first_month(df: pl.DataFrame, mask: pl.Expr) -> Optional[str]:
    hit = df.filter(mask).select("month")
    return None if hit.height == 0 else hit["month"][0]


def summary(df: pl.DataFrame, plan: Plan) -> dict:
    """Headline numbers for the dashboard cards."""
    last = df.row(-1, named=True)
    had_debt = (df["debts_total"] > _DEBT_EPS).any()
    debt_free = None
    if had_debt:
        # First month where debt is zero *after* the last month that carried debt.
        last_debt_idx = df.with_row_index().filter(pl.col("debts_total") > _DEBT_EPS)["index"].max()
        after = df.with_row_index().filter(
            (pl.col("index") > last_debt_idx) & (pl.col("debts_total") <= _DEBT_EPS)
        )
        debt_free = None if after.height == 0 else after["month"][0]
    elif df.height:
        debt_free = df["month"][0]

    min_row = df.sort("cash").row(0, named=True)
    return {
        "horizon_end": last["month"],
        "net_worth_end": last["net_worth"],
        "assets_end": last["assets_total"],
        "debts_end": last["debts_total"],
        "peak_net_worth": df["net_worth"].max(),
        "debt_free_month": debt_free,
        "min_cash": min_row["cash"],
        "min_cash_month": min_row["month"],
        "first_deficit_month": _first_month(df, pl.col("in_deficit")),
        "deficit_months": int(df["in_deficit"].sum()),
        "total_interest": df["interest"].sum(),
    }


def annual(df: pl.DataFrame) -> pl.DataFrame:
    """Calendar-year roll-up. Balances are year-end; flows are sums over the year."""
    flows = [
        "income_gross",
        "tax",
        "tax_income",
        "tax_fica",
        "income_net",
        "expenses",
        "interest",
        "minimum_payments",
        "extra_debt_payments",
        "lump_debt_payments",
        "recurring_contributions",
        "lump_contributions",
        "one_time_income",
        "one_time_expense",
    ]
    out = (
        df.with_columns(pl.col("month").str.slice(0, 4).alias("year"))
        .group_by("year", maintain_order=True)
        .agg(
            [pl.col(c).sum().alias(c) for c in flows]
            + [
                pl.col("net_worth").last().alias("net_worth_end"),
                pl.col("cash").last().alias("cash_end"),
                pl.col("assets_total").last().alias("assets_end"),
                pl.col("debts_total").last().alias("debts_end"),
                pl.col("in_deficit").any().alias("any_deficit"),
            ]
        )
    )
    # Savings = what is left of net income after living costs. Debt principal
    # paid counts as saving (it builds net worth), so only expenses reduce it.
    return out.with_columns(
        (pl.col("income_net") - pl.col("expenses")).alias("net_savings"),
        pl.when(pl.col("income_gross") > 0)
        .then(pl.col("tax") / pl.col("income_gross"))
        .otherwise(None)
        .alias("effective_tax_rate"),
        pl.when(pl.col("income_net") > 0)
        .then((pl.col("income_net") - pl.col("expenses")) / pl.col("income_net"))
        .otherwise(None)
        .alias("savings_rate"),
    )
