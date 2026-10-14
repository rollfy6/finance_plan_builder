"""Shared builders for engine, deflator, and tax tests.

``build`` makes a schema v2 plan from keyword lists of modules. Every module
defaults to the plan's start. Incomes and expenses default to the plan's end, so
``end=`` bounds the whole plan in one place.
"""

from fpb.engine import simulate
from fpb.models import Plan
from fpb.tax.base import ZERO_TAX


class NoTax:
    def monthly_tax(self, wages, month):
        return ZERO_TAX


def build(
    *,
    start="2026-01",
    end=None,
    eras=None,
    incomes=(),
    expenses=(),
    debts=(),
    assets=(),
    events=(),
    strategies=(),
    assumptions=(),
    **kw,
) -> Plan:
    data = {
        "schema_version": 2,
        "name": "t",
        "start": start,
        "base_year": 2026,
        "inflation": "0",
        "cash_return": "0",
    }
    data.update(kw)
    if eras is None:
        window = {"start": start, **({"end": end} if end else {})}
        eras = [{"id": "main", "label": "main", **window}]
    data["eras"] = eras
    plan_end = eras[-1].get("end")

    def windowed(items, default_end):
        out = []
        for item in items:
            item = dict(item)
            item.setdefault("start", start)
            if default_end and plan_end is not None:
                item.setdefault("end", plan_end)
            out.append(item)
        return out

    data["incomes"] = windowed(incomes, default_end=True)
    data["expenses"] = windowed(expenses, default_end=True)
    data["strategies"] = windowed(strategies, default_end=True)
    data["assumptions"] = windowed(assumptions, default_end=True)
    # Accounts persist to the end of the plan, so they take no end.
    data["debts"] = [{**dict(d), "start": d.get("start", start)} for d in debts]
    data["assets"] = [{**dict(a), "start": a.get("start", start)} for a in assets]
    data["events"] = list(events)
    return Plan.model_validate(data)


def sim(plan, horizon=None):
    return simulate(plan, horizon_end=horizon, tax_model=NoTax())
