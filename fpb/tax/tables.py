"""Loads per-year tax tables and applies inflation beyond the last table year.

Years with a table use that table exactly. Years after the last table use the
last table, with brackets, deductions, limits and the SS wage base grown by
(1 + inflation) ** years_elapsed. This mirrors IRS indexing in spirit (the IRS
indexes with chained CPI), but it is an assumption, not the IRS path.
Additional Medicare thresholds are statutory and are not indexed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

from fpb._embedded import TAX_TABLES
from fpb.money import ONE, q

DATA_DIR = Path(__file__).resolve().parent / "data"
CONTRIBUTION_GROUPS = ("401k", "ira", "hsa")


@dataclass(frozen=True)
class TaxYear:
    year: int
    std_deduction: dict[str, Decimal]
    brackets: dict[str, list[tuple[Decimal, Decimal]]]
    ss_rate: Decimal
    ss_wage_base: Decimal
    medicare_rate: Decimal
    addl_medicare_rate: Decimal
    addl_medicare_threshold: dict[str, Decimal]
    limits: dict[str, Decimal]
    catch_up_age: dict[str, int]


def _d(x) -> Decimal:
    return Decimal(str(x))


def _parse(raw: dict) -> TaxYear:
    return TaxYear(
        year=int(raw["tax_year"]),
        std_deduction={k: _d(v) for k, v in raw["std_deduction"].items()},
        brackets={
            k: [(_d(lo), _d(rate)) for lo, rate in rows] for k, rows in raw["brackets"].items()
        },
        ss_rate=_d(raw["social_security"]["rate"]),
        ss_wage_base=_d(raw["social_security"]["wage_base"]),
        medicare_rate=_d(raw["medicare"]["rate"]),
        addl_medicare_rate=_d(raw["medicare"]["additional_rate"]),
        addl_medicare_threshold={k: _d(v) for k, v in raw["medicare"]["additional_threshold"].items()},
        limits={k: _d(v) for k, v in raw["limits"].items()},
        catch_up_age={k: int(v) for k, v in raw["catch_up_age"].items()},
    )


class TaxTables:
    def __init__(self, years: list[TaxYear]):
        if not years:
            raise ValueError("no tax tables loaded")
        self.years = sorted(years, key=lambda t: t.year)

    @classmethod
    def load(cls, directory: Path = DATA_DIR) -> "TaxTables":
        """Load tables from ``directory``, or from the bundled copies if it is absent."""
        files = sorted(directory.glob("us_federal_*.yaml")) if directory.is_dir() else []
        if files:
            texts = [f.read_text(encoding="utf-8") for f in files]
        else:
            texts = list(TAX_TABLES.values())
        return cls([_parse(yaml.safe_load(t)) for t in texts])

    def for_year(self, year: int, inflation: Decimal) -> tuple[TaxYear, Decimal]:
        """Return the table to use and the indexing factor applied to its dollar amounts.

        Years before the earliest table use that table back-indexed by inflation. This
        is an approximation, not published IRS figures. It exists so plans that start
        before the earliest table can still be simulated. Add real tables to replace it.
        """
        candidates = [t for t in self.years if t.year <= year]
        table = candidates[-1] if candidates else self.years[0]
        return table, (ONE + inflation) ** (year - table.year)

    def contribution_limit(
        self, group: str, year: int, age: Optional[int], inflation: Decimal, hsa_coverage: str = "family"
    ) -> Decimal:
        """Annual employee-contribution limit for a group, including catch-up if eligible."""
        if group not in CONTRIBUTION_GROUPS:
            raise ValueError(f"unknown contribution group {group!r}")
        table, factor = self.for_year(year, inflation)
        base_key = f"hsa_{hsa_coverage}" if group == "hsa" else group
        limit = table.limits[base_key]
        if age is not None and age >= table.catch_up_age[group]:
            catch_key = f"{group}_catch_up"
            limit += table.limits[catch_key]
        return q(limit * factor)


@lru_cache(maxsize=1)
def default_tables() -> TaxTables:
    return TaxTables.load()
