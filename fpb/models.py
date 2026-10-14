"""Domain models for a financial plan (schema v2).

Conventions
-----------
* Money is ``Decimal`` (never float). Rates are annual decimals (0.055 = 5.5%).
* Months are ``"YYYY-MM"`` strings, compared through ``month_index``.
* Every module (income, expense, debt, asset, event, strategy, assumption) is a
  top-level entry with its own timing. Windows are inclusive; ``end`` omitted or
  null means open-ended.
* Eras are labels only. They name time bands for charts and reports and do not
  affect any calculation.
* Debts and assets are *accounts*. An account opens once, at ``start``, with an
  ``opening_balance``, and persists to the end of the plan. Its terms can change
  at later months through ``changes`` (for example a refinance or a new
  contribution rate). Balances carry through changes.
* All ids share one namespace across the plan and must be unique.
"""

from __future__ import annotations

import re
from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal, Optional

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

SCHEMA_VERSION = 2

Money = Annotated[Decimal, Field(ge=Decimal("0"))]
Rate = Annotated[Decimal, Field(ge=Decimal("0"), lt=Decimal("1"))]
Return = Annotated[Decimal, Field(gt=Decimal("-1"), lt=Decimal("1"))]  # may be negative
Share = Annotated[Decimal, Field(ge=Decimal("0"), le=Decimal("1"))]

_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def parse_month(value: str) -> tuple[int, int]:
    m = _MONTH_RE.match(value)
    if not m:
        raise ValueError(f"expected 'YYYY-MM', got {value!r}")
    return int(m.group(1)), int(m.group(2))


def month_index(value: str) -> int:
    """Months since year 0, so consecutive months differ by exactly 1."""
    year, month = parse_month(value)
    return year * 12 + (month - 1)


def index_to_month(idx: int) -> str:
    year, month0 = divmod(idx, 12)
    return f"{year:04d}-{month0 + 1:02d}"


def add_months(value: str, n: int) -> str:
    return index_to_month(month_index(value) + n)


def _check_id(value: str) -> str:
    if not _ID_RE.match(value):
        raise ValueError(f"id {value!r} must be snake_case and start with a letter")
    return value


def _check_month(value: str) -> str:
    parse_month(value)
    return value


Ident = Annotated[str, AfterValidator(_check_id)]
Month = Annotated[str, AfterValidator(_check_month)]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class _Window(_Base):
    """A module active from ``start`` through ``end`` (inclusive; None = open-ended)."""

    id: Ident
    label: str
    start: Month
    end: Optional[Month] = None

    @model_validator(mode="after")
    def _check_window(self) -> "_Window":
        if self.end is not None and month_index(self.end) < month_index(self.start):
            raise ValueError(f"{self.id!r}: end {self.end} is before start {self.start}")
        return self

    def active_in(self, month: str) -> bool:
        idx = month_index(month)
        if idx < month_index(self.start):
            return False
        return self.end is None or idx <= month_index(self.end)


class Person(_Base):
    id: Ident
    name: str
    birth_year: int = Field(ge=1900, le=2100)


class Era(_Window):
    """Labelled time band for charts and reports. No effect on calculations."""


class PayFrequency(str, Enum):
    monthly = "monthly"
    biweekly = "biweekly"
    semimonthly = "semimonthly"


class Income(_Window):
    """Gross recurring income. Taxes are applied by the engine."""

    owner: Optional[Ident] = None  # Person.id; None means joint
    gross_annual: Money
    annual_raise: Rate = Decimal("0")  # compounded each full year since this income's start
    pay_frequency: PayFrequency = PayFrequency.monthly


class Expense(_Window):
    """Recurring non-debt spending. Amount is in base-year dollars if inflation-linked."""

    category: str
    monthly: Money
    inflation_linked: bool = True


class InterestConvention(str, Enum):
    monthly_apr = "monthly_apr"  # APR / 12 on the balance each month (installment loans, mortgages)
    daily_apr = "daily_apr"  # APR / 365 * days in month (credit cards)


class DebtChange(_Base):
    """New terms for a debt from ``month`` onward. Unset fields keep their prior value."""

    month: Month
    apr: Optional[Rate] = None
    minimum_payment: Optional[Money] = None
    convention: Optional[InterestConvention] = None

    @model_validator(mode="after")
    def _has_field(self) -> "DebtChange":
        if self.apr is None and self.minimum_payment is None and self.convention is None:
            raise ValueError(f"debt change at {self.month} changes nothing")
        return self


class Debt(_Base):
    id: Ident
    label: str
    owner: Optional[Ident] = None
    start: Month  # month the account opens with opening_balance
    opening_balance: Money
    apr: Rate
    minimum_payment: Money
    convention: InterestConvention = InterestConvention.monthly_apr
    changes: list[DebtChange] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_debt(self) -> "Debt":
        if self.opening_balance > 0 and self.minimum_payment <= 0:
            raise ValueError(f"debt {self.id!r} has a balance but no minimum payment")
        _check_changes(self.id, self.start, [c.month for c in self.changes])
        return self


class AccountType(str, Enum):
    cash = "cash"
    taxable = "taxable"
    traditional_401k = "traditional_401k"
    roth_401k = "roth_401k"
    traditional_ira = "traditional_ira"
    roth_ira = "roth_ira"
    hsa = "hsa"
    real_estate = "real_estate"  # home or property; return = appreciation, not income


class AssetChange(_Base):
    """New terms for an asset from ``month`` onward. Unset fields keep their prior value."""

    month: Month
    annual_return: Optional[Return] = None
    monthly_contribution: Optional[Money] = None

    @model_validator(mode="after")
    def _has_field(self) -> "AssetChange":
        if self.annual_return is None and self.monthly_contribution is None:
            raise ValueError(f"asset change at {self.month} changes nothing")
        return self


class Asset(_Base):
    id: Ident
    label: str
    owner: Optional[Ident] = None
    account_type: AccountType = AccountType.taxable
    start: Month  # month the account opens with opening_balance
    opening_balance: Money
    annual_return: Return
    monthly_contribution: Money = Decimal("0")
    changes: list[AssetChange] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_asset(self) -> "Asset":
        _check_changes(self.id, self.start, [c.month for c in self.changes])
        return self


class EventKind(str, Enum):
    one_time_income = "one_time_income"  # bonus, gift, tax refund
    one_time_expense = "one_time_expense"  # purchase, repair, tuition
    lump_sum_debt_payment = "lump_sum_debt_payment"
    lump_sum_asset_contribution = "lump_sum_asset_contribution"
    asset_withdrawal = "asset_withdrawal"  # moves cash out of an asset into the cash bucket


_DEBT_EVENTS = {EventKind.lump_sum_debt_payment}
_ASSET_EVENTS = {EventKind.lump_sum_asset_contribution, EventKind.asset_withdrawal}


class Event(_Base):
    id: Ident
    label: str
    month: Month
    kind: EventKind
    amount: Money
    target_id: Optional[Ident] = None  # required for debt and asset events

    @model_validator(mode="after")
    def _target_matches_kind(self) -> "Event":
        needs_target = self.kind in _DEBT_EVENTS | _ASSET_EVENTS
        if needs_target and self.target_id is None:
            raise ValueError(f"event {self.id!r} of kind {self.kind.value} needs target_id")
        if not needs_target and self.target_id is not None:
            raise ValueError(f"event {self.id!r} of kind {self.kind.value} must not set target_id")
        return self


class BalanceSnapshot(_Base):
    """Actual balance from a bank or brokerage statement for one month.

    ``account`` is a debt or asset id, or ``cash`` for the cash bucket. Months with
    a snapshot use the actual balance for that account. Other months use projections.
    """

    month: Month
    account: str
    balance: Money


CASH_ACCOUNT = "cash"


class DebtStrategy(str, Enum):
    avalanche = "avalanche"  # highest APR first
    snowball = "snowball"  # smallest balance first
    fixed = "fixed"  # order given by debt_priority, then the rest by avalanche


class Strategy(_Window):
    """Pay-down rules for a window. Months outside every strategy use avalanche at 100%."""

    debt_strategy: DebtStrategy = DebtStrategy.avalanche
    debt_priority: list[Ident] = Field(default_factory=list)
    # Share of this month's positive net flow routed to extra debt payments.
    # The remainder stays in cash.
    surplus_to_debt: Share = Decimal("1")

    @model_validator(mode="after")
    def _check_strategy(self) -> "Strategy":
        if self.debt_strategy == DebtStrategy.fixed:
            if not self.debt_priority:
                raise ValueError(f"strategy {self.id!r}: fixed strategy needs a non-empty debt_priority")
        elif self.debt_priority:
            raise ValueError(f"strategy {self.id!r}: debt_priority only applies to the fixed strategy")
        if len(set(self.debt_priority)) != len(self.debt_priority):
            raise ValueError(f"strategy {self.id!r}: debt_priority has duplicates")
        return self


class Assumption(_Window):
    """Overrides of plan-level inflation and cash return for a window."""

    inflation: Optional[Rate] = None
    cash_return: Optional[Return] = None

    @model_validator(mode="after")
    def _has_field(self) -> "Assumption":
        if self.inflation is None and self.cash_return is None:
            raise ValueError(f"assumption {self.id!r} overrides nothing")
        return self


def _check_changes(owner: str, start: str, months: list[str]) -> None:
    prev = month_index(start)
    for m in months:
        idx = month_index(m)
        if idx <= prev:
            raise ValueError(f"{owner!r}: change months must be after {start} and strictly increasing")
        prev = idx


def _check_non_overlapping(kind: str, windows: list[_Window]) -> None:
    ordered = sorted(windows, key=lambda w: month_index(w.start))
    for prev, nxt in zip(ordered, ordered[1:]):
        if prev.end is None or month_index(nxt.start) <= month_index(prev.end):
            raise ValueError(f"{kind} {prev.id!r} and {nxt.id!r} overlap")


class Plan(_Base):
    schema_version: int = SCHEMA_VERSION
    name: str
    currency: Literal["USD"] = "USD"
    start: Month
    base_year: int = Field(ge=1900, le=2200, description="Price-level year for real-dollar output")
    inflation: Rate = Decimal("0.03")
    cash_return: Return = Decimal("0")
    opening_cash: Money = Decimal("0")
    persons: list[Person] = Field(default_factory=list)
    eras: list[Era] = Field(default_factory=list)
    strategies: list[Strategy] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    incomes: list[Income] = Field(default_factory=list)
    expenses: list[Expense] = Field(default_factory=list)
    debts: list[Debt] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    balance_history: list[BalanceSnapshot] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_plan(self) -> "Plan":
        if self.schema_version > SCHEMA_VERSION:
            raise ValueError(
                f"plan schema_version {self.schema_version} is newer than supported {SCHEMA_VERSION}"
            )
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"plan schema_version {self.schema_version} must be migrated to {SCHEMA_VERSION}"
            )
        if not self.eras:
            raise ValueError("plan must contain at least one era")
        self._check_timeline()
        self._check_ids()
        self._check_windows()
        self._check_references()
        return self

    # --- timeline -------------------------------------------------------

    def _check_timeline(self) -> None:
        if self.eras[0].start != self.start:
            raise ValueError(f"first era starts {self.eras[0].start}, plan starts {self.start}")
        for prev, nxt in zip(self.eras, self.eras[1:]):
            if prev.end is None:
                raise ValueError(f"era {prev.id!r} is open-ended but is not the last era")
            if month_index(nxt.start) != month_index(prev.end) + 1:
                raise ValueError(
                    f"eras {prev.id!r} and {nxt.id!r} are not contiguous ({prev.end} -> {nxt.start})"
                )

    def _check_windows(self) -> None:
        last = self.end
        for mod in (*self.incomes, *self.expenses, *self.strategies, *self.assumptions):
            if month_index(mod.start) < month_index(self.start):
                raise ValueError(f"{mod.id!r} starts {mod.start}, before plan start {self.start}")
            if last is not None and mod.end is not None and month_index(mod.end) > month_index(last):
                raise ValueError(f"{mod.id!r} ends {mod.end}, after plan end {last}")
        for acct in (*self.debts, *self.assets):
            if month_index(acct.start) < month_index(self.start):
                raise ValueError(f"{acct.id!r} opens {acct.start}, before plan start {self.start}")
            for ch in acct.changes:
                if last is not None and month_index(ch.month) > month_index(last):
                    raise ValueError(f"{acct.id!r} change at {ch.month}, after plan end {last}")
        for ev in self.events:
            if last is not None and month_index(ev.month) > month_index(last):
                raise ValueError(f"event {ev.id!r} at {ev.month}, after plan end {last}")
        _check_non_overlapping("strategy", self.strategies)
        _check_non_overlapping("assumption", self.assumptions)

    # --- ids and references ----------------------------------------------

    def _check_ids(self) -> None:
        claimed: dict[str, str] = {}

        def claim(kind: str, ident: str) -> None:
            if ident in claimed:
                raise ValueError(f"duplicate id {ident!r} ({kind}; already used by {claimed[ident]})")
            claimed[ident] = kind

        for kind, items in (
            ("person", self.persons),
            ("era", self.eras),
            ("income", self.incomes),
            ("expense", self.expenses),
            ("debt", self.debts),
            ("asset", self.assets),
            ("event", self.events),
            ("strategy", self.strategies),
            ("assumption", self.assumptions),
        ):
            for item in items:
                claim(kind, item.id)

    def _check_references(self) -> None:
        person_ids = {p.id for p in self.persons}
        debts = {d.id: d for d in self.debts}
        assets = {a.id: a for a in self.assets}

        for owned in (*self.incomes, *self.debts, *self.assets):
            if owned.owner is not None and owned.owner not in person_ids:
                raise ValueError(f"{owned.id!r} references unknown owner {owned.owner!r}")

        for ev in self.events:
            if ev.month < self.start:
                raise ValueError(f"event {ev.id!r} at {ev.month} is before plan start")
            if ev.kind in _DEBT_EVENTS:
                if ev.target_id not in debts:
                    raise ValueError(f"event {ev.id!r} targets debt {ev.target_id!r}, which is not defined")
                if month_index(debts[ev.target_id].start) > month_index(ev.month):
                    raise ValueError(f"event {ev.id!r} targets debt {ev.target_id!r} before it opens")
            if ev.kind in _ASSET_EVENTS:
                if ev.target_id not in assets:
                    raise ValueError(f"event {ev.id!r} targets asset {ev.target_id!r}, which is not defined")
                if month_index(assets[ev.target_id].start) > month_index(ev.month):
                    raise ValueError(f"event {ev.id!r} targets asset {ev.target_id!r} before it opens")

        for s in self.strategies:
            for dep in s.debt_priority:
                if dep not in debts:
                    raise ValueError(f"strategy {s.id!r}: debt_priority names unknown debt {dep!r}")

        if CASH_ACCOUNT in debts or CASH_ACCOUNT in assets:
            raise ValueError(f"{CASH_ACCOUNT!r} is reserved for the cash bucket")
        seen: set[tuple[str, str]] = set()
        for snap in self.balance_history:
            key = (snap.month, snap.account)
            if key in seen:
                raise ValueError(f"duplicate balance for {snap.account!r} in {snap.month}")
            seen.add(key)
            if month_index(snap.month) < month_index(self.start):
                raise ValueError(f"balance for {snap.account!r} at {snap.month} is before plan start")
            if self.end is not None and month_index(snap.month) > month_index(self.end):
                raise ValueError(f"balance for {snap.account!r} at {snap.month} is after plan end")
            if snap.account == CASH_ACCOUNT:
                continue
            acct = debts.get(snap.account) or assets.get(snap.account)
            if acct is None:
                raise ValueError(f"balance history names unknown account {snap.account!r}")
            if month_index(acct.start) > month_index(snap.month):
                raise ValueError(f"balance for {snap.account!r} at {snap.month} is before the account opens")

    # --- lookups ----------------------------------------------------------

    @property
    def end(self) -> Optional[str]:
        """Last month covered by the plan, or None if the final era is open-ended."""
        return self.eras[-1].end

    def era_for_month(self, month: str) -> Era:
        for era in self.eras:
            if era.active_in(month):
                return era
        raise ValueError(f"no era covers {month}")

    def strategy_for_month(self, month: str) -> Optional[Strategy]:
        for s in self.strategies:
            if s.active_in(month):
                return s
        return None

    def assumption_for_month(self, month: str) -> Optional[Assumption]:
        for a in self.assumptions:
            if a.active_in(month):
                return a
        return None

    def months(self, horizon_end: Optional[str] = None) -> list[str]:
        """Enumerate months from plan start through ``horizon_end`` (or plan end)."""
        last = horizon_end or self.end
        if last is None:
            raise ValueError("open-ended plan needs an explicit horizon_end")
        return [index_to_month(i) for i in range(month_index(self.start), month_index(last) + 1)]
