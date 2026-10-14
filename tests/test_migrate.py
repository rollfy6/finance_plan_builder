from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest
import yaml

from fpb.engine import simulate
from fpb.migrate import MigrationError, migrate_v1
from fpb.storage import load_plan, plan_from_dict, save_plan

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "plans" / "example.yaml"
V1_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "example_v1.yaml"
GOLDEN = ROOT / "tests" / "golden" / "example_v1_2050.csv"


def _v1() -> dict:
    return yaml.safe_load(V1_FIXTURE.read_text())


def test_migrated_v1_example_reproduces_golden_output_exactly():
    golden = pl.read_csv(GOLDEN)
    df = simulate(load_plan(V1_FIXTURE), horizon_end="2050-12").drop("source")
    assert set(df.columns) == set(golden.columns)
    for c in golden.columns:
        if golden[c].dtype.is_numeric() and df[c].dtype.is_numeric():
            assert df[c].to_list() == pytest.approx(golden[c].to_list(), abs=1e-6), c
        else:
            assert df[c].to_list() == golden[c].to_list(), c


def test_shipped_example_is_v2_household_plan():
    plan = load_plan(EXAMPLE)
    assert plan.schema_version == 2
    assert [p.id for p in plan.persons] == ["brittany", "mark"]
    assert plan.start == "2023-06"


def test_v1_eras_become_labels_and_modules_become_top_level():
    plan = load_plan(V1_FIXTURE)
    assert [e.id for e in plan.eras] == ["early_career", "home_purchase", "family_peak"]
    assert {i.id for i in plan.incomes} >= {"alex_salary", "alex_salary_2", "alex_salary_3"}
    # v1 windows become module windows with the same bounds.
    alex_2 = next(i for i in plan.incomes if i.id == "alex_salary_2")
    assert (alex_2.start, alex_2.end) == ("2028-01", "2033-12")


def test_repeated_v1_ids_are_suffixed_with_era():
    plan = load_plan(V1_FIXTURE)
    groceries = sorted(e.id for e in plan.expenses if e.category == "food")
    assert groceries == ["groceries", "groceries_family_peak", "groceries_home_purchase"]


def test_each_v1_era_becomes_a_strategy_window():
    plan = load_plan(V1_FIXTURE)
    s = {x.id: x for x in plan.strategies}
    assert (s["early_career_strategy"].start, s["early_career_strategy"].end) == ("2026-01", "2027-12")
    assert s["early_career_strategy"].surplus_to_debt == Decimal("0.4")
    assert s["home_purchase_strategy"].debt_strategy.value == "snowball"
    assert s["family_peak_strategy"].debt_priority == ["credit_card", "mortgage"]


def test_v1_era_assumptions_become_assumption_window():
    plan = load_plan(V1_FIXTURE)
    a = plan.assumption_for_month("2035-01")
    assert a is not None and float(a.inflation) == 0.025
    assert plan.assumption_for_month("2030-01") is None


def test_v1_account_listing_becomes_change_at_era_start():
    plan = load_plan(V1_FIXTURE)
    mortgage = next(d for d in plan.debts if d.id == "mortgage")
    assert mortgage.start == "2028-01"
    assert [c.month for c in mortgage.changes] == ["2034-01"]
    assert float(mortgage.changes[0].apr) == 0.0525


def test_v1_contribution_stop_becomes_asset_change():
    plan = load_plan(V1_FIXTURE)
    fund = next(a for a in plan.assets if a.id == "house_fund")
    assert fund.start == "2026-01"
    assert [(c.month, float(c.monthly_contribution)) for c in fund.changes] == [("2028-01", 0.0)]


def test_migration_output_is_v2_and_round_trips(tmp_path):
    plan = load_plan(V1_FIXTURE)
    out = tmp_path / "migrated.yaml"
    save_plan(plan, out)
    assert yaml.safe_load(out.read_text())["schema_version"] == 2
    assert load_plan(out) == plan


def test_plan_from_dict_accepts_v1_dicts():
    assert plan_from_dict(_v1()).schema_version == 2


def test_override_that_changes_owner_is_rejected():
    data = _v1()
    data["eras"][2]["debts"][0]["owner"] = "sam"  # family_peak restates mortgage; it has no owner
    with pytest.raises(MigrationError, match="owner"):
        migrate_v1(data)


def test_override_of_unopened_account_is_rejected():
    data = _v1()
    data["eras"][0]["debts"] = [
        {"id": "ghost", "label": "g", "apr": "0.1", "minimum_payment": "10"}
    ]
    with pytest.raises(MigrationError, match="never opened"):
        migrate_v1(data)


def test_v1_without_eras_is_rejected():
    with pytest.raises(MigrationError, match="at least one era"):
        migrate_v1({"name": "x", "start": "2026-01", "base_year": 2026, "eras": []})
