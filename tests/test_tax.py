from decimal import Decimal
from pathlib import Path

import pytest

from fpb.engine import simulate
from fpb.storage import load_plan
from fpb.tax.base import FlatEffectiveTax, PersonWage
from fpb.tax.federal import USFederalTax, progressive
from fpb.tax.tables import TaxTables, default_tables
from tests.helpers import build

EXAMPLE = Path(__file__).resolve().parent.parent / "plans" / "example.yaml"
D = Decimal


def w(pid, annual, pretax_annual=0):
    return PersonWage(pid, D(annual) / 12, D(pretax_annual) / 12)


@pytest.fixture(scope="module")
def fed():
    return USFederalTax(default_tables(), inflation=D("0.03"))


# --- brackets --------------------------------------------------------------

def test_progressive_single_bracket_segments():
    rows = [(D(0), D("0.10")), (D(12400), D("0.12")), (D(50400), D("0.22"))]
    # 10% of 12400 + 12% of 38000 + 22% of 49600
    assert progressive(D(100000), rows) == D("1240") + D("4560") + D("10912")


def test_progressive_zero_income():
    assert progressive(D(0), [(D(0), D("0.10"))]) == 0


# --- federal income tax ----------------------------------------------------

def test_single_2026_income_tax_matches_hand_calc(fed):
    # 116,100 - 16,100 std = 100,000 taxable -> 16,712 annual
    r = fed.monthly_tax([w("a", 116100)], "2026-03")
    assert r.income_tax == D("1392.67")


def test_mfj_2026_income_tax_matches_hand_calc(fed):
    # 232,200 - 32,200 = 200,000 taxable -> 33,424 annual
    r = fed.monthly_tax([w("a", 116100), w("b", 116100)], "2026-03")
    assert r.income_tax == D("2785.33")


def test_pretax_deferral_reduces_income_tax_not_fica(fed):
    no_defer = fed.monthly_tax([w("a", 124600)], "2026-03")
    defer = fed.monthly_tax([w("a", 124600, 24500)], "2026-03")
    # taxable 84,000 -> 13,192 annual vs 16,712 + 2 * ... compare to direct calc
    assert defer.income_tax == D("1099.33")
    assert defer.fica == no_defer.fica


def test_social_security_stops_at_wage_base(fed):
    # 300,000 wages: SS only on 184,500; Medicare on all; additional Medicare above 200k
    r = fed.monthly_tax([w("a", 300000)], "2026-03")
    expected = D("0.062") * D("184500") + D("0.0145") * D("300000") + D("0.009") * D("100000")
    assert r.fica * 12 == pytest.approx(expected, abs=12)


def test_additional_medicare_uses_joint_threshold(fed):
    # Two earners at 150k each: each below 200k, but combined 300k is over the 250k MFJ threshold.
    r = fed.monthly_tax([w("a", 150000), w("b", 150000)], "2026-03")
    ss = 2 * D("0.062") * D("150000")  # both under the wage base
    med = 2 * D("0.0145") * D("150000")
    addl = D("0.009") * D("50000")
    assert r.fica * 12 == pytest.approx(ss + med + addl, abs=12)


def test_zero_wages_zero_tax(fed):
    r = fed.monthly_tax([w("a", 0)], "2026-03")
    assert r.total == 0


def test_no_persons_uses_household_key(fed):
    # 40,000 - 16,100 = 23,900 taxable -> 1240 + 12% * 11,500 = 2,620 annual
    r = fed.monthly_tax([PersonWage("household", D(40000) / 12, D(0))], "2026-03")
    assert r.income_tax == D("218.33")


def test_income_below_standard_deduction_owes_no_income_tax(fed):
    r = fed.monthly_tax([w("a", 10000)], "2026-03")
    assert r.income_tax == 0
    assert r.fica > 0


def test_inflation_indexes_standard_deduction_after_table_year(fed):
    # 2027 has no table, so 2026 is used with factor 1.03: std = 16,583.
    income = D("116100")
    tables = default_tables()
    t27 = USFederalTax(tables, inflation=D("0.03")).monthly_tax([w("a", income)], "2027-03")
    t26 = USFederalTax(tables, inflation=D("0.03")).monthly_tax([w("a", income)], "2026-03")
    assert t27.income_tax < t26.income_tax  # more deduction and brackets scaled up


def test_2025_table_is_separate_and_lower_deduction():
    tables = default_tables()
    t25, f25 = tables.for_year(2025, D("0.03"))
    assert t25.year == 2025 and f25 == 1
    assert t25.std_deduction["single"] == D("15750")
    assert t25.ss_wage_base == D("176100")


def test_year_before_earliest_table_is_back_indexed_approximation():
    table, factor = default_tables().for_year(2023, D("0.03"))
    assert table.year == 2025
    assert float(factor) == pytest.approx(1.03 ** -2, rel=1e-12)


def test_flat_effective_tax_still_available():
    r = FlatEffectiveTax(D("0.25")).monthly_tax([w("a", 120000)], "2026-01")
    assert r.income_tax == D("2500.00") and r.fica == 0


# --- contribution limits ---------------------------------------------------

def test_401k_limit_with_and_without_catch_up():
    t = default_tables()
    assert t.contribution_limit("401k", 2026, 40, D("0.03")) == D("24500")
    assert t.contribution_limit("401k", 2026, 50, D("0.03")) == D("32500")


def test_hsa_catch_up_starts_at_55_not_50():
    t = default_tables()
    assert t.contribution_limit("hsa", 2026, 54, D("0.03"), "self") == D("4400")
    assert t.contribution_limit("hsa", 2026, 55, D("0.03"), "self") == D("5400")
    assert t.contribution_limit("hsa", 2026, 40, D("0.03"), "family") == D("8750")


def test_limits_index_after_table_year():
    t = default_tables()
    assert t.contribution_limit("401k", 2027, 40, D("0.03")) == D("25235")


def test_unknown_contribution_group_rejected():
    with pytest.raises(ValueError):
        default_tables().contribution_limit("bogus", 2026, 40, D("0.03"))


def test_tables_load_from_directory_only_federal_files():
    t = TaxTables.load()
    assert [y.year for y in t.years] == [2025, 2026]


# --- engine integration ----------------------------------------------------

def test_engine_caps_401k_over_limit_and_flags_it():
    p = build(end="2026-12", assets=[
        {"id": "k", "label": "k", "owner": "alex", "account_type": "traditional_401k",
         "opening_balance": "0", "annual_return": "0", "monthly_contribution": "3000"}],
        persons=[{"id": "alex", "name": "Alex", "birth_year": 1990}])
    df = simulate(p, horizon_end="2026-12", tax_model=None)
    # 3000 * 12 = 36000 > 24500: capped to 24500/12 per month.
    assert df["asset__k"][-1] == pytest.approx(24500.0, abs=0.12)
    assert df["contribution_capped"].all()


def test_engine_under_limit_is_not_flagged():
    p = build(end="2026-12", assets=[
        {"id": "k", "label": "k", "owner": "alex", "account_type": "traditional_401k",
         "opening_balance": "0", "annual_return": "0", "monthly_contribution": "1000"}],
        persons=[{"id": "alex", "name": "Alex", "birth_year": 1990}])
    df = simulate(p, horizon_end="2026-12")
    assert not df["contribution_capped"].any()
    assert df["asset__k"][-1] == 12000.0


def test_401k_and_roth_share_one_limit():
    p = build(end="2026-12", assets=[
        {"id": "k", "label": "k", "owner": "alex", "account_type": "traditional_401k",
         "opening_balance": "0", "annual_return": "0", "monthly_contribution": "1500"},
        {"id": "r", "label": "r", "owner": "alex", "account_type": "roth_401k",
         "opening_balance": "0", "annual_return": "0", "monthly_contribution": "1500"}],
        persons=[{"id": "alex", "name": "Alex", "birth_year": 1990}])
    df = simulate(p, horizon_end="2026-12")
    total = df["asset__k"][-1] + df["asset__r"][-1]
    assert total == pytest.approx(24500.0, abs=0.2)


def test_catch_up_eligibility_uses_birth_year():
    # Born 1970: age 56 in 2026, so the 401k limit is 32,500.
    p = build(end="2026-12", assets=[
        {"id": "k", "label": "k", "owner": "alex", "account_type": "traditional_401k",
         "opening_balance": "0", "annual_return": "0", "monthly_contribution": "2700"}],
        persons=[{"id": "alex", "name": "Alex", "birth_year": 1970}])
    df = simulate(p, horizon_end="2026-12")
    assert not df["contribution_capped"].any()


def test_traditional_401k_reduces_income_tax_in_engine():
    base_inc = [{"id": "s", "label": "s", "owner": "alex", "gross_annual": "150000"}]
    persons = [{"id": "alex", "name": "Alex", "birth_year": 1990}]
    no_k = build(end="2026-12", incomes=base_inc, persons=persons)
    with_k = build(end="2026-12", incomes=base_inc, assets=[
        {"id": "k", "label": "k", "owner": "alex", "account_type": "traditional_401k",
         "opening_balance": "0", "annual_return": "0", "monthly_contribution": "2000"}],
        persons=persons)
    a = simulate(no_k, horizon_end="2026-12")
    b = simulate(with_k, horizon_end="2026-12")
    assert b["tax_income"][0] < a["tax_income"][0]
    assert b["tax_fica"][0] == a["tax_fica"][0]  # deferrals are still FICA wages


def test_roth_401k_does_not_reduce_income_tax():
    base_inc = [{"id": "s", "label": "s", "owner": "alex", "gross_annual": "150000"}]
    persons = [{"id": "alex", "name": "Alex", "birth_year": 1990}]
    no_k = build(end="2026-12", incomes=base_inc, persons=persons)
    roth = build(end="2026-12", incomes=base_inc, assets=[
        {"id": "k", "label": "k", "owner": "alex", "account_type": "roth_401k",
         "opening_balance": "0", "annual_return": "0", "monthly_contribution": "2000"}],
        persons=persons)
    assert simulate(no_k, horizon_end="2026-12")["tax_income"][0] == \
        simulate(roth, horizon_end="2026-12")["tax_income"][0]


def test_joint_income_split_between_two_persons():
    p = build(end="2026-12", incomes=[
        {"id": "j", "label": "j", "gross_annual": "120000"}],
        persons=[{"id": "alex", "name": "A", "birth_year": 1990},
                 {"id": "sam", "name": "S", "birth_year": 1990}])
    df = simulate(p, horizon_end="2026-12")
    # Joint income is one household total; split does not change the gross.
    assert df["income_gross"][0] == 10000.0


def test_example_plan_tax_is_plausible_for_2026():
    plan = load_plan(EXAMPLE)
    df = simulate(plan, horizon_end="2026-12")
    gross = df["income_gross"][0]
    # Two earners: 98k + 64k gross (plus 401k deferrals). Effective total tax
    # should be well under the old 25% placeholder and above zero.
    eff = df["tax"][0] / gross
    assert 0.10 < eff < 0.22, eff
    assert df["tax_fica"][0] > 0 and df["tax_income"][0] > 0
