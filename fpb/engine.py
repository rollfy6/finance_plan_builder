"""Monthly simulation engine.

Pure Python with Decimal arithmetic. No UI imports. ``simulate`` returns one
wide Polars DataFrame with one row per month in nominal dollars. Real dollars
are a view (see ``fpb.deflator``).

Order of operations within a month
----------------------------------
1. Accounts that open this month are added, and term changes due this month
   are applied. Balances carry through changes.
2. Growth on opening balances: cash return, then asset returns (annual / 12).
3. Interest accrues on opening debt balances (monthly_apr or daily_apr).
4. Active incomes and expenses for the month are summed. Recurring contributions are capped at annual limits per owner and group.
   Each person's wages and pre-tax deferrals go to the TaxModel, which returns
   income tax and FICA. Recurring expenses are paid from net income.
5. Minimum debt payments and recurring asset contributions are paid.
6. One-time events run in list order.
7. This month's net flow, if positive, is routed to extra debt payments by the
   active strategy (avalanche at 100% when none is active), times ``surplus_to_debt``. The remainder stays in cash.
7b. Actual balances from ``balance_history`` replace the simulated balances for
   the listed accounts this month. Unlisted accounts keep their projection.
8. Balances are recorded. Negative cash is reported as a deficit, never clipped.
   ``source`` is "actual" for months with any balance_history entry, else "projected".

Known simplifications (to be addressed in later milestones)
-----------------------------------------------------------
* One-time income (bonuses, gifts) is not taxed.
* Taxes are computed on annualized monthly wages, so mid-year raises or
  start/stop dates are approximated.
* ``pay_frequency`` is stored but cash moves monthly.
* Withdrawals from retirement accounts are not taxed or penalized.
* Overdrafts accrue no interest.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import polars as pl

from fpb.deflator import price_index
from fpb.models import (
    CASH_ACCOUNT,
    AccountType,
    DebtStrategy,
    EventKind,
    InterestConvention,
    Person,
    Plan,
    add_months,
    month_index,
    parse_month,
)
from fpb.money import DAYS_PER_YEAR, ONE, TWELVE, ZERO, q
from fpb.tax.base import PersonWage, TaxModel
from fpb.tax.federal import USFederalTax
from fpb.tax.tables import TaxTables, default_tables

DEFAULT_HORIZON_MONTHS = 40 * 12

_CONTRIB_GROUP = {
    AccountType.traditional_401k: "401k",
    AccountType.roth_401k: "401k",
    AccountType.traditional_ira: "ira",
    AccountType.roth_ira: "ira",
    AccountType.hsa: "hsa",
}
_PRETAX_TYPES = {AccountType.traditional_401k, AccountType.hsa}
_JOINT = "household"  # taxpayer key when the plan lists no persons


def _contribution_group(account_type: AccountType) -> Optional[str]:
    return _CONTRIB_GROUP.get(account_type)


def _age_in_year(persons: list[Person], owner: Optional[str], year: int) -> Optional[int]:
    for p in persons:
        if p.id == owner:
            return year - p.birth_year
    return None


def _capped_contributions(
    assets: dict,
    persons: list[Person],
    tables: TaxTables,
    year: int,
    inflation: Decimal,
) -> tuple[dict[str, Decimal], bool]:
    """Monthly contribution per asset after annual limits, and whether any was reduced.

    Accounts in one group (401k: traditional + roth; ira: traditional + roth) share
    one limit per owner. When the requested total exceeds it, every contribution in
    that group is scaled down pro rata.
    """
    amounts = {a.id: a.monthly_contribution for a in assets.values() if a.monthly_contribution > 0}
    groups: dict[tuple[str, Optional[str]], list[str]] = defaultdict(list)
    for a in assets.values():
        g = _contribution_group(a.account_type)
        if g is not None and a.id in amounts:
            groups[(g, a.owner)].append(a.id)

    capped = False
    for (group, owner), ids in groups.items():
        age = _age_in_year(persons, owner, year)
        monthly_cap = tables.contribution_limit(group, year, age, inflation) / TWELVE
        requested = sum((amounts[i] for i in ids), ZERO)
        if requested > monthly_cap:
            capped = True
            scale = monthly_cap / requested
            for i in ids:
                amounts[i] = q(amounts[i] * scale)
    return amounts, capped


def _split_joint(amount: Decimal, keys: list[str]) -> dict[str, Decimal]:
    """Split a joint amount equally across taxpayers; the first key takes the cent remainder."""
    base = q(amount / len(keys))
    out = {k: base for k in keys}
    out[keys[0]] += amount - base * len(keys)
    return out


@dataclass
class _Debt:
    id: str
    label: str
    owner: Optional[str]
    apr: Decimal
    minimum_payment: Decimal
    convention: InterestConvention
    balance: Decimal


@dataclass
class _Asset:
    id: str
    label: str
    owner: Optional[str]
    account_type: AccountType
    annual_return: Decimal
    monthly_contribution: Decimal
    balance: Decimal


def _apply_account_updates(
    plan: Plan, m: str, debts: dict[str, _Debt], assets: dict[str, _Asset]
) -> None:
    """Open accounts that start this month and apply term changes due this month.

    Balances carry through term changes. Changes always fall after an account's
    start (validated), so opening and changing never happen in the same month.
    """
    for d in plan.debts:
        if d.start == m:
            debts[d.id] = _Debt(
                id=d.id,
                label=d.label,
                owner=d.owner,
                apr=d.apr,
                minimum_payment=d.minimum_payment,
                convention=d.convention,
                balance=d.opening_balance,
            )
        for ch in d.changes:
            if ch.month == m:
                cur = debts[d.id]
                if ch.apr is not None:
                    cur.apr = ch.apr
                if ch.minimum_payment is not None:
                    cur.minimum_payment = ch.minimum_payment
                if ch.convention is not None:
                    cur.convention = ch.convention
    for a in plan.assets:
        if a.start == m:
            assets[a.id] = _Asset(
                id=a.id,
                label=a.label,
                owner=a.owner,
                account_type=a.account_type,
                annual_return=a.annual_return,
                monthly_contribution=a.monthly_contribution,
                balance=a.opening_balance,
            )
        for ch in a.changes:
            if ch.month == m:
                cur = assets[a.id]
                if ch.annual_return is not None:
                    cur.annual_return = ch.annual_return
                if ch.monthly_contribution is not None:
                    cur.monthly_contribution = ch.monthly_contribution


def _interest(d: _Debt, month: str) -> Decimal:
    if d.convention == InterestConvention.monthly_apr:
        raw = d.balance * d.apr / TWELVE
    else:
        year, mon = parse_month(month)
        days = calendar.monthrange(year, mon)[1]
        raw = d.balance * d.apr / DAYS_PER_YEAR * Decimal(days)
    return q(raw)


def _debt_order(debts: list[_Debt], strategy: DebtStrategy, priority: list[str]) -> list[_Debt]:
    live = [d for d in debts if d.balance > 0]
    avalanche = sorted(live, key=lambda d: (-d.apr, d.balance, d.id))
    if strategy == DebtStrategy.avalanche:
        return avalanche
    if strategy == DebtStrategy.snowball:
        return sorted(live, key=lambda d: (d.balance, -d.apr, d.id))
    # fixed: explicit priority first, remaining debts by avalanche
    by_id = {d.id: d for d in live}
    head = [by_id[i] for i in priority if i in by_id]
    listed = set(priority)
    return head + [d for d in avalanche if d.id not in listed]


def simulate(
    plan: Plan,
    horizon_end: Optional[str] = None,
    tax_model: Optional[TaxModel] = None,
    tables: Optional[TaxTables] = None,
) -> pl.DataFrame:
    """Run the plan month by month. Returns one row per month, nominal dollars."""
    tables = tables or default_tables()
    tax = tax_model or USFederalTax(tables, inflation=plan.inflation)
    taxpayer_keys = [p.id for p in plan.persons] or [_JOINT]

    if plan.end is not None and horizon_end is not None and month_index(horizon_end) > month_index(plan.end):
        raise ValueError(f"horizon {horizon_end} runs past plan end {plan.end}")
    last = horizon_end or plan.end or add_months(plan.start, DEFAULT_HORIZON_MONTHS - 1)
    months = plan.months(last)
    prices = price_index(plan, last)

    actuals: dict[str, list] = defaultdict(list)
    for snap in plan.balance_history:
        actuals[snap.month].append(snap)

    cash = plan.opening_cash
    debts: dict[str, _Debt] = {}
    assets: dict[str, _Asset] = {}
    rows: list[dict] = []

    for m in months:
        _apply_account_updates(plan, m, debts, assets)
        era = plan.era_for_month(m)
        strat = plan.strategy_for_month(m)
        assumption = plan.assumption_for_month(m)
        debt_strategy = strat.debt_strategy if strat else DebtStrategy.avalanche
        debt_priority = strat.debt_priority if strat else []
        surplus_share = strat.surplus_to_debt if strat else ONE

        # Explicit None check: a 0% override must not fall back to the plan default.
        cash_return = (
            assumption.cash_return
            if assumption is not None and assumption.cash_return is not None
            else plan.cash_return
        )

        row: dict = {
            "month": m,
            "era_id": era.id,
            "price_index": float(prices[m]),
            "source": "actual" if m in actuals else "projected",
        }

        # 2. growth on opening balances
        cash_growth = q(cash * cash_return / TWELVE) if cash > 0 else ZERO
        cash += cash_growth
        asset_growth = ZERO
        for a in assets.values():
            g = q(a.balance * a.annual_return / TWELVE)
            a.balance += g
            asset_growth += g

        # 3. interest on opening debt balances
        interest = ZERO
        for d in debts.values():
            if d.balance > 0:
                i = _interest(d, m)
                d.balance += i
                interest += i

        # 4. recurring contributions (capped), pre-tax deferrals, income and taxes
        year = int(m[:4])
        contrib, capped_flag = _capped_contributions(
            assets, plan.persons, tables, year, plan.inflation
        )
        gross_by: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for inc in plan.incomes:
            if not inc.active_in(m):
                continue
            # Raises compound once per full year since this income's own start.
            years = (month_index(m) - month_index(inc.start)) // 12
            amt = q(inc.gross_annual * (ONE + inc.annual_raise) ** years / TWELVE)
            if inc.owner is None:
                for k, share in _split_joint(amt, taxpayer_keys).items():
                    gross_by[k] += share
            else:
                gross_by[inc.owner] += amt
        pretax_by: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for a in assets.values():
            if a.account_type in _PRETAX_TYPES and a.owner is not None:
                pretax_by[a.owner] += contrib.get(a.id, ZERO)
        wages = [
            PersonWage(k, gross_by.get(k, ZERO), pretax_by.get(k, ZERO)) for k in taxpayer_keys
        ]
        gross = sum((w.gross_monthly for w in wages), ZERO)
        breakdown = tax.monthly_tax(wages, m)
        tax_due = breakdown.total
        net = gross - tax_due

        expenses = ZERO
        exp_by_cat: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for e in plan.expenses:
            if not e.active_in(m):
                continue
            amt = q(e.monthly * prices[m]) if e.inflation_linked else e.monthly
            expenses += amt
            exp_by_cat[e.category] += amt

        # 5. minimum debt payments and recurring contributions
        minimums = ZERO
        for d in debts.values():
            if d.balance > 0 and d.minimum_payment > 0:
                p = min(d.minimum_payment, d.balance)
                d.balance -= p
                minimums += p
        contributions = ZERO
        for a in assets.values():
            c = contrib.get(a.id, ZERO)
            if c > 0:
                a.balance += c
                contributions += c

        cash += net - expenses - minimums - contributions

        # 6. one-time events, in list order
        one_time_in = ZERO
        one_time_out = ZERO
        lump_debt = ZERO
        lump_contrib = ZERO
        withdrawals = ZERO
        shortfall = ZERO
        for ev in plan.events:
            if ev.month != m:
                continue
            amt = ev.amount
            if ev.kind == EventKind.one_time_income:
                cash += amt
                one_time_in += amt
            elif ev.kind == EventKind.one_time_expense:
                cash -= amt
                one_time_out += amt
            elif ev.kind == EventKind.lump_sum_debt_payment:
                d = debts[ev.target_id]
                p = min(amt, d.balance) if d.balance > 0 else ZERO
                d.balance -= p
                cash -= p
                lump_debt += p
            elif ev.kind == EventKind.lump_sum_asset_contribution:
                a = assets[ev.target_id]
                a.balance += amt
                cash -= amt
                lump_contrib += amt
            elif ev.kind == EventKind.asset_withdrawal:
                a = assets[ev.target_id]
                w = min(amt, max(a.balance, ZERO))
                a.balance -= w
                cash += w
                withdrawals += w
                shortfall += amt - w

        # 7. route positive net flow to extra debt payments
        surplus = net + one_time_in - expenses - one_time_out - minimums - contributions
        extra_budget = min(q(max(surplus, ZERO) * surplus_share), max(cash, ZERO))
        extra_paid = ZERO
        for d in _debt_order(list(debts.values()), debt_strategy, debt_priority):
            if extra_budget <= 0:
                break
            p = min(extra_budget, d.balance)
            d.balance -= p
            extra_budget -= p
            extra_paid += p
        cash -= extra_paid

        # 7b. actual balances from statements replace simulated balances for this month.
        # Later months continue from these values. Unlisted accounts keep their projection.
        for snap in actuals.get(m, []):
            if snap.account == CASH_ACCOUNT:
                cash = snap.balance
            elif snap.account in debts:
                debts[snap.account].balance = snap.balance
            else:
                assets[snap.account].balance = snap.balance

        # 8. record
        assets_total = sum((a.balance for a in assets.values()), ZERO)
        debts_total = sum((d.balance for d in debts.values()), ZERO)
        row.update(
            {
                "cash": float(cash),
                "assets_total": float(assets_total),
                "debts_total": float(debts_total),
                "net_worth": float(cash + assets_total - debts_total),
                "income_gross": float(gross),
                "tax": float(tax_due),
                "tax_income": float(breakdown.income_tax),
                "tax_fica": float(breakdown.fica),
                "contribution_capped": capped_flag,
                "income_net": float(net),
                "expenses": float(expenses),
                "interest": float(interest),
                "minimum_payments": float(minimums),
                "extra_debt_payments": float(extra_paid),
                "lump_debt_payments": float(lump_debt),
                "recurring_contributions": float(contributions),
                "lump_contributions": float(lump_contrib),
                "one_time_income": float(one_time_in),
                "one_time_expense": float(one_time_out),
                "withdrawals": float(withdrawals),
                "asset_growth": float(asset_growth),
                "cash_growth": float(cash_growth),
                "surplus": float(surplus),
                "event_shortfall": float(shortfall),
                "deficit": float(max(-cash, ZERO)),
                "in_deficit": cash < 0,
            }
        )
        for d in debts.values():
            row[f"debt__{d.id}"] = float(d.balance)
        for a in assets.values():
            row[f"asset__{a.id}"] = float(a.balance)
        for cat, amt in exp_by_cat.items():
            row[f"exp__{cat}"] = float(amt)
        rows.append(row)

    df = pl.DataFrame(rows, infer_schema_length=None)
    float_cols = [c for c, dt in df.schema.items() if dt == pl.Float64]
    return df.with_columns([pl.col(c).fill_null(0.0) for c in float_cols])
