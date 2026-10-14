"""US federal income tax and FICA, computed per filing unit per tax year.

Assumptions (see README of the plan for the full list):
* Filing status: MFJ when two or more persons earn wages, otherwise single.
  Head of household is not modeled.
* Income tax uses wages minus traditional 401(k) and HSA deferrals, less the
  standard deduction. Roth deferrals and traditional IRA contributions are not
  deducted (IRA deductibility phase-outs depend on workplace coverage and are
  not modeled).
* FICA uses gross wages. Social Security stops at the wage base per person.
  Additional Medicare is assessed on combined wages for MFJ.
* No credits (child tax credit, EITC, etc.), no itemizing, no capital gains,
  no NIIT, no AMT.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from fpb.money import ZERO, TWELVE, q
from fpb.tax.base import ZERO_TAX, PersonWage, TaxBreakdown
from fpb.tax.tables import TaxTables, default_tables


def progressive(taxable: Decimal, brackets: list[tuple[Decimal, Decimal]]) -> Decimal:
    """Tax on ``taxable`` under bracket rows of (threshold, rate), thresholds ascending."""
    total = ZERO
    for i, (lo, rate) in enumerate(brackets):
        if taxable <= lo:
            break
        hi = brackets[i + 1][0] if i + 1 < len(brackets) else None
        top = taxable if hi is None else min(taxable, hi)
        total += (top - lo) * rate
    return total


class USFederalTax:
    def __init__(self, tables: TaxTables | None = None, inflation: Decimal = Decimal("0.03")):
        self.tables = tables or default_tables()
        self.inflation = inflation

    def monthly_tax(self, wages: Sequence[PersonWage], month: str) -> TaxBreakdown:
        if not wages or all(w.gross_monthly == 0 for w in wages):
            return ZERO_TAX

        year = int(month[:4])
        table, f = self.tables.for_year(year, self.inflation)
        filing = "mfj" if len({w.person_id for w in wages}) >= 2 else "single"

        gross_by_person = {w.person_id: TWELVE * w.gross_monthly for w in wages}
        gross_total = sum(gross_by_person.values(), ZERO)
        pretax_total = TWELVE * sum((w.pretax_monthly for w in wages), ZERO)

        # Income tax on the filing unit.
        std = table.std_deduction[filing] * f
        taxable = max(ZERO, gross_total - pretax_total - std)
        brackets = [(lo * f, rate) for lo, rate in table.brackets[filing]]
        income_tax_annual = progressive(taxable, brackets)

        # FICA per person; Additional Medicare on the filing unit.
        ss_base = table.ss_wage_base * f
        fica_annual = ZERO
        for g in gross_by_person.values():
            fica_annual += table.ss_rate * min(g, ss_base) + table.medicare_rate * g
        threshold = table.addl_medicare_threshold[filing]
        if filing == "mfj":
            excess = max(ZERO, gross_total - threshold)
        else:
            excess = sum((max(ZERO, g - threshold) for g in gross_by_person.values()), ZERO)
        fica_annual += table.addl_medicare_rate * excess

        return TaxBreakdown(
            income_tax=q(income_tax_annual / TWELVE),
            fica=q(fica_annual / TWELVE),
        )
