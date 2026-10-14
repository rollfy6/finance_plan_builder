"""Tax interface used by the engine.

The engine reports each person's wages and pre-tax deferrals for one month and
receives the tax owed for the household that month. Tax is computed on
annualized figures and divided by 12, so a steady salary is taxed exactly and
mid-year changes are approximated.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, Sequence

from fpb.money import ZERO, q


@dataclass(frozen=True)
class PersonWage:
    person_id: str
    gross_monthly: Decimal  # W-2 wages before any deferral
    pretax_monthly: Decimal  # traditional 401(k) and HSA payroll deferrals


@dataclass(frozen=True)
class TaxBreakdown:
    income_tax: Decimal
    fica: Decimal

    @property
    def total(self) -> Decimal:
        return self.income_tax + self.fica


ZERO_TAX = TaxBreakdown(ZERO, ZERO)


class TaxModel(Protocol):
    def monthly_tax(self, wages: Sequence[PersonWage], month: str) -> TaxBreakdown: ...


@dataclass(frozen=True)
class FlatEffectiveTax:
    """Scaffolding only: one effective rate on gross wages, no FICA, no deductions."""

    rate: Decimal = Decimal("0.25")

    def __post_init__(self) -> None:
        if not (Decimal("0") <= self.rate < Decimal("1")):
            raise ValueError("tax rate must be in [0, 1)")

    def monthly_tax(self, wages: Sequence[PersonWage], month: str) -> TaxBreakdown:
        gross = sum((w.gross_monthly for w in wages), ZERO)
        return TaxBreakdown(income_tax=q(gross * self.rate), fica=ZERO)
