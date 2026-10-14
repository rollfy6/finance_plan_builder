"""Price index and nominal -> real conversion.

The index is 1 in January of ``plan.base_year``. Each month it grows by
(1 + annual inflation) ** (1/12), using the active assumption window's inflation where set, else the
plan default. Plan amounts are stated in base-year dollars, so the same index
drives inflation-linked expenses in the engine.

Real figures are a *view*: the simulation runs once in nominal dollars, and
``to_real`` divides money columns by the ``price_index`` column.
"""

from __future__ import annotations

from decimal import Decimal

import polars as pl

from fpb.models import Plan, index_to_month, month_index
from fpb.money import ONE, TWELVE


def _monthly_factor(plan: Plan, idx: int) -> Decimal:
    month = index_to_month(idx)
    rate = plan.inflation
    assumption = plan.assumption_for_month(month)
    if assumption is not None and assumption.inflation is not None:
        rate = assumption.inflation
    return (ONE + rate) ** (ONE / TWELVE)


def price_index(plan: Plan, last: str) -> dict[str, Decimal]:
    """Price index for every month from plan.start through ``last``."""
    anchor = month_index(f"{plan.base_year:04d}-01")
    lo = month_index(plan.start)
    hi = month_index(last)

    values: dict[int, Decimal] = {anchor: ONE}
    for i in range(anchor, hi):
        values[i + 1] = values[i] * _monthly_factor(plan, i)
    for i in range(anchor, lo, -1):
        values[i - 1] = values[i] / _monthly_factor(plan, i - 1)

    return {index_to_month(i): values[i] for i in range(lo, hi + 1)}


def to_real(df: pl.DataFrame) -> pl.DataFrame:
    """Return a copy with every money column divided by ``price_index``."""
    money_cols = [
        c for c, dt in df.schema.items() if dt == pl.Float64 and c != "price_index"
    ]
    return df.with_columns([(pl.col(c) / pl.col("price_index")).alias(c) for c in money_cols])


def view(df: pl.DataFrame, real: bool) -> pl.DataFrame:
    """Nominal or real-dollar view of a simulation frame."""
    return to_real(df) if real else df
