from decimal import Decimal

import pytest
from pydantic import ValidationError

from fpb.models import (
    Debt,
    Event,
    EventKind,
    Plan,
    add_months,
    index_to_month,
    month_index,
)


def _plan(**overrides):
    base = {
        "schema_version": 2,
        "name": "t",
        "start": "2026-01",
        "base_year": 2026,
        "persons": [{"id": "alex", "name": "Alex", "birth_year": 1990}],
        "eras": [
            {"id": "e1", "label": "one", "start": "2026-01", "end": "2026-12"},
            {"id": "e2", "label": "two", "start": "2027-01"},
        ],
        "debts": [
            {
                "id": "cc",
                "label": "card",
                "start": "2026-01",
                "opening_balance": "1000",
                "apr": "0.2",
                "minimum_payment": "50",
                "convention": "daily_apr",
            }
        ],
    }
    base.update(overrides)
    return base


# --- month helpers -------------------------------------------------------

def test_month_arithmetic_crosses_year_boundary():
    assert add_months("2026-11", 3) == "2027-02"
    assert month_index("2027-01") - month_index("2026-12") == 1
    assert index_to_month(month_index("2031-07")) == "2031-07"


@pytest.mark.parametrize("bad", ["2026-13", "2026-00", "26-01", "2026/01", "2026-1"])
def test_bad_month_strings_rejected(bad):
    with pytest.raises(ValidationError):
        Plan.model_validate(_plan(start=bad))


# --- money and rates -----------------------------------------------------

def test_money_is_decimal_not_float():
    p = Plan.model_validate(_plan())
    assert isinstance(p.debts[0].opening_balance, Decimal)
    assert p.debts[0].apr == Decimal("0.2")


def test_negative_balance_rejected():
    with pytest.raises(ValidationError):
        Debt(id="x", label="x", start="2026-01", opening_balance=-1, apr="0.1", minimum_payment="10")


def test_rate_must_be_fraction_not_percent():
    # 20 (meaning 20%) is a common mistake and must not pass silently.
    with pytest.raises(ValidationError):
        Debt(id="x", label="x", start="2026-01", opening_balance=100, apr="20", minimum_payment="10")


def test_balance_without_minimum_payment_rejected():
    with pytest.raises(ValidationError):
        Debt(id="x", label="x", start="2026-01", opening_balance=100, apr="0.1", minimum_payment="0")


def test_zero_balance_debt_needs_no_payment():
    d = Debt(id="x", label="x", start="2026-01", opening_balance="0", apr="0.05", minimum_payment="0")
    assert d.opening_balance == 0


# --- ids -----------------------------------------------------------------

@pytest.mark.parametrize("bad", ["Alex", "1st", "has space", "dash-id", ""])
def test_ids_must_be_snake_case(bad):
    with pytest.raises(ValidationError):
        Plan.model_validate(_plan(persons=[{"id": bad, "name": "x", "birth_year": 1990}]))


def test_ids_are_unique_across_all_module_kinds():
    data = _plan()
    data["debts"].append({"id": "e1", "label": "dup", "start": "2026-01", "opening_balance": "5",
                          "apr": "0.1", "minimum_payment": "1"})
    with pytest.raises(ValidationError, match="duplicate id 'e1'"):
        Plan.model_validate(data)


def test_duplicate_income_id_rejected():
    data = _plan()
    inc = {"id": "salary", "label": "s", "start": "2026-01", "gross_annual": "100000"}
    data["incomes"] = [inc, dict(inc)]
    with pytest.raises(ValidationError, match="duplicate id 'salary'"):
        Plan.model_validate(data)


# --- eras as labels -------------------------------------------------------

def test_gap_between_eras_rejected():
    data = _plan()
    data["eras"][1]["start"] = "2027-02"
    with pytest.raises(ValidationError, match="not contiguous"):
        Plan.model_validate(data)


def test_overlapping_eras_rejected():
    data = _plan()
    data["eras"][1]["start"] = "2026-06"
    with pytest.raises(ValidationError, match="not contiguous"):
        Plan.model_validate(data)


def test_first_era_must_start_at_plan_start():
    data = _plan(start="2025-12")
    with pytest.raises(ValidationError, match="plan starts"):
        Plan.model_validate(data)


def test_open_ended_era_only_allowed_last():
    data = _plan()
    data["eras"][0]["end"] = None
    with pytest.raises(ValidationError, match="open-ended"):
        Plan.model_validate(data)


def test_era_end_before_start_rejected():
    from fpb.models import Era

    with pytest.raises(ValidationError, match="before start"):
        Era(id="x", label="x", start="2026-05", end="2026-04")


def test_era_for_month_and_months():
    p = Plan.model_validate(_plan())
    assert p.era_for_month("2026-12").id == "e1"
    assert p.era_for_month("2027-01").id == "e2"
    assert len(p.months("2027-03")) == 15
    with pytest.raises(ValueError, match="open-ended"):
        p.months()


def test_eras_do_not_scope_modules():
    # An income with a window inside one era is fine; eras never gate modules.
    data = _plan()
    data["incomes"] = [{"id": "s", "label": "s", "start": "2026-03", "end": "2027-06",
                        "gross_annual": "1000"}]
    p = Plan.model_validate(data)
    assert p.incomes[0].active_in("2027-06")
    assert not p.incomes[0].active_in("2027-07")


# --- module windows ----------------------------------------------------------

def test_module_end_before_start_rejected():
    data = _plan()
    data["expenses"] = [{"id": "x", "label": "x", "start": "2026-06", "end": "2026-02",
                         "category": "c", "monthly": "1"}]
    with pytest.raises(ValidationError, match="before start"):
        Plan.model_validate(data)


def test_module_before_plan_start_rejected():
    data = _plan()
    data["incomes"] = [{"id": "s", "label": "s", "start": "2025-06", "gross_annual": "1"}]
    with pytest.raises(ValidationError, match="before plan start"):
        Plan.model_validate(data)


def test_module_past_closed_plan_end_rejected():
    data = _plan()
    data["eras"][1]["end"] = "2027-12"
    data["expenses"] = [{"id": "x", "label": "x", "start": "2027-01", "end": "2028-01",
                         "category": "c", "monthly": "1"}]
    with pytest.raises(ValidationError, match="after plan end"):
        Plan.model_validate(data)


def test_overlapping_strategies_rejected():
    data = _plan()
    data["strategies"] = [
        {"id": "s1", "label": "a", "start": "2026-01", "end": "2026-06"},
        {"id": "s2", "label": "b", "start": "2026-06", "end": "2026-12"},
    ]
    with pytest.raises(ValidationError, match="overlap"):
        Plan.model_validate(data)


def test_adjacent_strategies_allowed_and_selected_by_month():
    data = _plan()
    data["strategies"] = [
        {"id": "s1", "label": "a", "start": "2026-01", "end": "2026-06", "debt_strategy": "snowball"},
        {"id": "s2", "label": "b", "start": "2026-07", "end": "2026-12", "debt_strategy": "avalanche"},
    ]
    p = Plan.model_validate(data)
    assert p.strategy_for_month("2026-06").id == "s1"
    assert p.strategy_for_month("2026-07").id == "s2"
    assert p.strategy_for_month("2027-03") is None


def test_overlapping_assumptions_rejected():
    data = _plan()
    data["assumptions"] = [
        {"id": "a1", "label": "a", "start": "2026-01", "inflation": "0.02"},
        {"id": "a2", "label": "b", "start": "2027-01", "inflation": "0.04"},
    ]
    with pytest.raises(ValidationError, match="overlap"):
        Plan.model_validate(data)


def test_assumption_must_override_something():
    data = _plan()
    data["assumptions"] = [{"id": "a1", "label": "a", "start": "2026-01"}]
    with pytest.raises(ValidationError, match="overrides nothing"):
        Plan.model_validate(data)


# --- references ----------------------------------------------------------

def test_owner_must_be_known_person():
    data = _plan()
    data["incomes"] = [{"id": "s", "label": "s", "start": "2026-01", "owner": "nobody", "gross_annual": "1000"}]
    with pytest.raises(ValidationError, match="unknown owner"):
        Plan.model_validate(data)


def test_fixed_strategy_requires_priority_on_known_debts():
    data = _plan()
    data["strategies"] = [{"id": "s", "label": "s", "start": "2027-01", "debt_strategy": "fixed",
                           "debt_priority": ["cc"]}]
    Plan.model_validate(data)  # cc is defined, so valid

    data["strategies"][0]["debt_priority"] = ["nope"]
    with pytest.raises(ValidationError, match="unknown debt"):
        Plan.model_validate(data)


def test_fixed_strategy_without_priority_rejected():
    data = _plan()
    data["strategies"] = [{"id": "s", "label": "s", "start": "2026-01", "debt_strategy": "fixed"}]
    with pytest.raises(ValidationError, match="needs a non-empty debt_priority"):
        Plan.model_validate(data)


def test_priority_without_fixed_strategy_rejected():
    data = _plan()
    data["strategies"] = [{"id": "s", "label": "s", "start": "2026-01", "debt_priority": ["cc"]}]
    with pytest.raises(ValidationError, match="only applies"):
        Plan.model_validate(data)


# --- account changes -----------------------------------------------------

def test_debt_change_restates_terms_at_its_month():
    data = _plan()
    data["debts"][0]["changes"] = [{"month": "2027-01", "apr": "0.06", "minimum_payment": "100"}]
    p = Plan.model_validate(data)
    assert p.debts[0].changes[0].apr == Decimal("0.06")


def test_debt_change_must_be_after_start_and_increasing():
    data = _plan()
    data["debts"][0]["changes"] = [{"month": "2026-01", "apr": "0.06"}]
    with pytest.raises(ValidationError, match="strictly increasing"):
        Plan.model_validate(data)

    data["debts"][0]["changes"] = [{"month": "2027-06", "apr": "0.06"}, {"month": "2027-03", "apr": "0.05"}]
    with pytest.raises(ValidationError, match="strictly increasing"):
        Plan.model_validate(data)


def test_empty_change_rejected():
    data = _plan()
    data["debts"][0]["changes"] = [{"month": "2027-01"}]
    with pytest.raises(ValidationError, match="changes nothing"):
        Plan.model_validate(data)


def test_change_after_plan_end_rejected():
    data = _plan()
    data["eras"][1]["end"] = "2027-12"
    data["debts"][0]["changes"] = [{"month": "2028-01", "apr": "0.06"}]
    with pytest.raises(ValidationError, match="after plan end"):
        Plan.model_validate(data)


# --- events --------------------------------------------------------------

def test_debt_event_needs_known_debt_target():
    data = _plan()
    data["events"] = [
        {"id": "pay", "label": "p", "month": "2026-06", "kind": "lump_sum_debt_payment",
         "amount": "500", "target_id": "missing"}
    ]
    with pytest.raises(ValidationError, match="targets debt"):
        Plan.model_validate(data)


def test_event_before_target_opens_rejected():
    data = _plan()
    data["debts"][0]["start"] = "2026-06"
    data["debts"][0]["opening_balance"] = "1000"
    data["events"] = [
        {"id": "pay", "label": "p", "month": "2026-02", "kind": "lump_sum_debt_payment",
         "amount": "500", "target_id": "cc"}
    ]
    with pytest.raises(ValidationError, match="before it opens"):
        Plan.model_validate(data)


def test_event_before_plan_start_rejected():
    data = _plan()
    data["events"] = [{"id": "b", "label": "b", "month": "2025-06", "kind": "one_time_income", "amount": "1"}]
    with pytest.raises(ValidationError, match="before plan start"):
        Plan.model_validate(data)


def test_event_target_rules_by_kind():
    with pytest.raises(ValidationError, match="needs target_id"):
        Event(id="e", label="e", month="2026-01", kind=EventKind.asset_withdrawal, amount="1")
    with pytest.raises(ValidationError, match="must not set target_id"):
        Event(id="e", label="e", month="2026-01", kind=EventKind.one_time_income, amount="1",
              target_id="x")


# --- schema version and strictness ----------------------------------------

def test_schema_version_from_future_rejected():
    with pytest.raises(ValidationError, match="newer than supported"):
        Plan.model_validate(_plan(schema_version=99))


def test_v1_dict_must_go_through_migration():
    with pytest.raises(ValidationError, match="must be migrated"):
        Plan.model_validate(_plan(schema_version=1))


def test_extra_fields_rejected_to_catch_typos():
    data = _plan()
    data["debts"][0]["aprr"] = "0.1"
    with pytest.raises(ValidationError, match="extra"):
        Plan.model_validate(data)
