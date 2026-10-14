from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from fpb.engine import simulate
from fpb.money import q
from fpb.storage import load_plan, plan_from_dict, plan_to_dict
from tests.helpers import NoTax, build, sim

EXAMPLE = Path(__file__).resolve().parent.parent / "plans" / "example.yaml"

NO_SURPLUS = [{"id": "no_surplus", "label": "no surplus", "surplus_to_debt": "0"}]


# --- invariants ----------------------------------------------------------

def test_net_worth_identity_on_example_every_month():
    df = simulate(load_plan(EXAMPLE), horizon_end="2060-12")
    debt_cols = [c for c in df.columns if c.startswith("debt__")]
    asset_cols = [c for c in df.columns if c.startswith("asset__")]
    assets = df.select(pl.sum_horizontal(asset_cols)).to_series()
    debts = df.select(pl.sum_horizontal(debt_cols)).to_series()
    assert (df["assets_total"] - assets).abs().max() < 1e-6
    assert (df["debts_total"] - debts).abs().max() < 1e-6
    expected_nw = df["cash"] + df["assets_total"] - df["debts_total"]
    assert (df["net_worth"] - expected_nw).abs().max() < 1e-6


def test_debts_never_negative_on_example():
    df = simulate(load_plan(EXAMPLE), horizon_end="2060-12")
    for c in df.columns:
        if c.startswith("debt__"):
            assert df[c].min() >= 0, c


def test_example_has_no_deficit_under_base_assumptions():
    df = simulate(load_plan(EXAMPLE), horizon_end="2060-12")
    assert not df["in_deficit"].any()


def test_prefix_unchanged_when_later_income_changes():
    base = load_plan(EXAMPLE)
    data = plan_to_dict(base)
    later = next(i for i in data["incomes"] if i["id"] == "mark_salary_3")
    later["gross_annual"] = 1
    changed = plan_from_dict(data)
    a = sim(base, "2040-12").filter(pl.col("month") < "2034-01")
    b = sim(changed, "2040-12").filter(pl.col("month") < "2034-01")
    assert a.to_dicts() == b.to_dicts()


def test_horizon_past_closed_plan_end_rejected():
    p = build(end="2026-12")
    with pytest.raises(ValueError, match="past plan end"):
        sim(p, "2027-06")


# --- growth and closed forms ----------------------------------------------

def test_zero_activity_asset_grows_monthly_exactly():
    p = build(end="2026-12", assets=[
        {"id": "inv", "label": "inv", "opening_balance": "1000", "annual_return": "0.12"}
    ])
    df = sim(p)
    bal = Decimal("1000")
    for _ in range(12):
        bal += q(bal * Decimal("0.12") / 12)
    assert df["asset__inv"][-1] == float(bal)
    assert df["cash"].max() == 0.0 and df["cash"].min() == 0.0
    assert df["income_net"].sum() == 0.0


def test_amortization_matches_closed_form():
    P = Decimal("10000")
    r = Decimal("0.12") / 12
    n = 12
    pmt = q(P * r / (1 - (1 + r) ** -n))
    p = build(end="2026-12", strategies=NO_SURPLUS, debts=[
        {"id": "loan", "label": "loan", "opening_balance": "10000", "apr": "0.12",
         "minimum_payment": str(pmt), "convention": "monthly_apr"}
    ])
    bal = sim(p)["debt__loan"].to_list()
    for k in range(1, n):
        closed = P * (1 + r) ** k - pmt * ((1 + r) ** k - 1) / r
        # Each month rounds interest to cents, so error accumulates a few cents at most.
        assert abs(bal[k - 1] - float(closed)) < 0.25, (k, bal[k - 1], float(closed))
    assert abs(bal[-1]) < 0.25


def test_daily_apr_uses_days_in_month():
    # February 2026 has 28 days; a 365-day year at 36.5% gives exactly 0.1 * days per month.
    p = build(end="2026-03", strategies=NO_SURPLUS, debts=[
        {"id": "cc", "label": "cc", "opening_balance": "1000", "apr": "0.365",
         "minimum_payment": "10", "convention": "daily_apr"}
    ])
    df = sim(p)
    # Jan: 31 days -> interest 1000*0.365/365*31 = 31.00; payment 10 -> balance 1021.00
    assert df["interest"][0] == 31.0
    assert df["debt__cc"][0] == 1021.0
    # Feb: 28 days -> 1021 * 0.001 * 28 = 28.588 -> 28.59
    assert df["interest"][1] == 28.59


def test_monthly_apr_interest_is_balance_times_rate_over_12():
    p = build(end="2026-01", strategies=NO_SURPLUS, debts=[
        {"id": "m", "label": "m", "opening_balance": "12000", "apr": "0.06", "minimum_payment": "100"}
    ])
    df = sim(p)
    assert df["interest"][0] == 60.0


# --- debt strategies -------------------------------------------------------
# Net income 8500 (NoTax), rent 6000, minimums 150 -> surplus 2350 each month.
# Card: 5000 at 20%, min 100. Loan: 1000 at 5%, min 50.
# Month-1 interest: card 83.33, loan 4.17.

def _strategy_plan(strategy=None, priority=None):
    strat = {"id": "s", "label": "s"}
    if strategy:
        strat["debt_strategy"] = strategy
    if priority:
        strat["debt_priority"] = priority
    return build(
        end="2026-03",
        incomes=[{"id": "pay", "label": "pay", "gross_annual": "102000"}],
        expenses=[{"id": "rent", "label": "rent", "category": "x", "monthly": "6000",
                   "inflation_linked": False}],
        debts=[
            {"id": "card", "label": "card", "opening_balance": "5000", "apr": "0.20", "minimum_payment": "100"},
            {"id": "loan", "label": "loan", "opening_balance": "1000", "apr": "0.05", "minimum_payment": "50"},
        ],
        strategies=[strat],
    )


def test_avalanche_sends_extra_to_highest_apr_debt():
    df = sim(_strategy_plan("avalanche"))
    assert df["debt__card"][0] == pytest.approx(5000 + 83.33 - 100 - 2350, abs=0.01)
    assert df["debt__loan"][0] == pytest.approx(1000 + 4.17 - 50, abs=0.01)


def test_snowball_clears_smallest_balance_first():
    df = sim(_strategy_plan("snowball"))
    assert df["debt__loan"][0] == 0.0
    assert df["debt__card"][0] == pytest.approx(5000 + 83.33 - 100 - (2350 - (1000 + 4.17 - 50)), abs=0.01)


def test_fixed_priority_overrides_apr_order():
    df = sim(_strategy_plan("fixed", ["loan", "card"]))
    assert df["debt__loan"][0] == 0.0


def test_fixed_priority_matching_avalanche_matches_avalanche():
    a = sim(_strategy_plan("avalanche"))
    f = sim(_strategy_plan("fixed", ["card", "loan"]))
    assert a.to_dicts() == f.to_dicts()


def test_no_active_strategy_defaults_to_avalanche_at_full_surplus():
    no_strategy = sim(_strategy_plan())
    explicit = sim(_strategy_plan("avalanche"))
    assert no_strategy.to_dicts() == explicit.to_dicts()


def test_strategy_window_switches_order_mid_plan():
    p = build(
        end="2026-03",
        incomes=[{"id": "pay", "label": "pay", "gross_annual": "102000"}],
        expenses=[{"id": "rent", "label": "rent", "category": "x", "monthly": "6000",
                   "inflation_linked": False}],
        debts=[
            {"id": "card", "label": "card", "opening_balance": "5000", "apr": "0.20", "minimum_payment": "100"},
            {"id": "loan", "label": "loan", "opening_balance": "1000", "apr": "0.05", "minimum_payment": "50"},
        ],
        strategies=[
            {"id": "early", "label": "early", "start": "2026-01", "end": "2026-01", "debt_strategy": "snowball"},
            {"id": "late", "label": "late", "start": "2026-02", "end": "2026-03", "debt_strategy": "avalanche"},
        ],
    )
    df = sim(p)
    assert df["debt__loan"][0] == 0.0  # snowball cleared the smaller loan in January
    assert df["debt__card"][1] < df["debt__card"][0]  # avalanche then works on the card


def test_surplus_share_zero_keeps_cash_and_no_extra_payments():
    p = build(
        end="2026-03",
        strategies=NO_SURPLUS,
        incomes=[{"id": "pay", "label": "pay", "gross_annual": "120000"}],
        debts=[{"id": "loan", "label": "loan", "opening_balance": "1000", "apr": "0", "minimum_payment": "10"}],
    )
    df = sim(p)
    assert df["extra_debt_payments"].sum() == 0.0
    assert df["cash"][-1] > 0


def test_cash_return_zero_override_does_not_fall_back_to_plan_default():
    p = build(end="2026-12", assumptions=[{"id": "a", "label": "a", "cash_return": "0"}],
              cash_return="0.12", opening_cash="1000")
    df = sim(p)
    assert df["cash_growth"].sum() == 0.0
    assert df["cash"][-1] == 1000.0


def test_cash_return_applies_when_no_override():
    p = build(end="2026-12", cash_return="0.12", opening_cash="1000")
    df = sim(p)
    assert df["cash_growth"][0] == 10.0


# --- module windows -------------------------------------------------------

def test_income_starts_mid_plan():
    p = build(end="2026-06", incomes=[{"id": "s", "label": "s", "start": "2026-03", "gross_annual": "12000"}])
    df = sim(p)
    assert df["income_gross"][1] == 0.0
    assert df["income_gross"][2] == 1000.0


def test_income_ends_mid_plan():
    p = build(end="2026-06", incomes=[{"id": "s", "label": "s", "end": "2026-02", "gross_annual": "12000"}])
    df = sim(p)
    assert df["income_gross"][1] == 1000.0
    assert df["income_gross"][2] == 0.0


def test_expense_window_stops_spending_when_it_ends():
    p = build(end="2026-06", expenses=[
        {"id": "gym", "label": "gym", "start": "2026-01", "end": "2026-02", "category": "lifestyle",
         "monthly": "50", "inflation_linked": False}])
    df = sim(p)
    assert df["exp__lifestyle"][1] == 50.0
    assert df["exp__lifestyle"][2] == 0.0


def test_income_raise_compounds_from_the_income_own_start():
    p = build(end="2027-12", incomes=[
        {"id": "s", "label": "s", "start": "2026-07", "gross_annual": "12000", "annual_raise": "0.10"}])
    df = sim(p)
    assert df["income_gross"][6] == 1000.0  # July 2026
    assert df["income_gross"][18] == 1100.0  # July 2027: one full year later


def test_debt_opened_mid_plan_is_zero_before_its_start():
    p = build(end="2026-06", strategies=NO_SURPLUS, debts=[
        {"id": "late", "label": "late", "start": "2026-04", "opening_balance": "1000", "apr": "0",
         "minimum_payment": "100"}])
    df = sim(p)
    assert df["debt__late"][2] == 0.0
    assert df["debt__late"][3] == 900.0


def test_asset_opened_mid_plan_grows_from_its_start():
    p = build(end="2026-03", assets=[
        {"id": "home", "label": "home", "start": "2026-02", "opening_balance": "1000", "annual_return": "0"}])
    df = sim(p)
    assert df["asset__home"][0] == 0.0
    assert df["asset__home"][1] == 1000.0


def test_account_persists_to_plan_end_without_any_window():
    p = build(end="2026-06", strategies=NO_SURPLUS, debts=[
        {"id": "m", "label": "m", "opening_balance": "1000", "apr": "0", "minimum_payment": "100"}])
    df = sim(p)
    assert (df["debt__m"][:-1] > df["debt__m"][1:]).all()


# --- cash, deficits, events ------------------------------------------------

def test_deficit_is_reported_not_clipped():
    p = build(end="2026-02", expenses=[
        {"id": "x", "label": "x", "category": "c", "monthly": "500", "inflation_linked": False}
    ])
    df = sim(p)
    assert df["cash"][0] == -500.0
    assert df["deficit"][0] == 500.0
    assert bool(df["in_deficit"][0]) is True


def test_one_time_expense_reduces_net_worth_exactly():
    p = build(end="2026-03", events=[
        {"id": "fix", "label": "fix", "month": "2026-02", "kind": "one_time_expense", "amount": "500"}
    ])
    df = sim(p)
    assert df["net_worth"][1] - df["net_worth"][0] == -500.0


def test_one_time_income_adds_exactly_and_is_routed_to_debt():
    p = build(end="2026-03", debts=[
        {"id": "loan", "label": "loan", "opening_balance": "1000", "apr": "0", "minimum_payment": "10"}
    ], events=[
        {"id": "bonus", "label": "bonus", "month": "2026-02", "kind": "one_time_income", "amount": "400"}
    ])
    df = sim(p)
    assert df["one_time_income"][1] == 400.0
    # Month 1: min 10 paid from cash (cash -10). Month 2: min 10, then 380 of the bonus
    # is routed to debt (capped by available cash), leaving 600.
    assert df["debt__loan"][1] == 600.0
    assert df["net_worth"][1] - df["net_worth"][0] == 400.0


def test_asset_withdrawal_moves_money_without_changing_net_worth():
    p = build(end="2026-03",
              assets=[{"id": "fund", "label": "fund", "opening_balance": "1000", "annual_return": "0"}],
              events=[{"id": "w", "label": "w", "month": "2026-02", "kind": "asset_withdrawal",
                       "amount": "400", "target_id": "fund"}])
    df = sim(p)
    assert df["cash"][1] == 400.0
    assert df["asset__fund"][1] == 600.0
    assert df["net_worth"][1] == df["net_worth"][0]


def test_asset_withdrawal_capped_and_shortfall_recorded():
    p = build(end="2026-02",
              assets=[{"id": "fund", "label": "fund", "opening_balance": "1000", "annual_return": "0"}],
              events=[{"id": "w", "label": "w", "month": "2026-02", "kind": "asset_withdrawal",
                       "amount": "5000", "target_id": "fund"}])
    df = sim(p)
    assert df["withdrawals"][1] == 1000.0
    assert df["event_shortfall"][1] == 4000.0
    assert df["asset__fund"][1] == 0.0


def test_lump_debt_payment_leaves_net_worth_flat():
    p = build(end="2026-03",
              debts=[{"id": "loan", "label": "loan", "opening_balance": "1000", "apr": "0",
                      "minimum_payment": "10"}],
              events=[{"id": "pay", "label": "pay", "month": "2026-02",
                       "kind": "lump_sum_debt_payment", "amount": "300", "target_id": "loan"}],
              opening_cash="1000", strategies=NO_SURPLUS)
    df = sim(p)
    assert df["lump_debt_payments"][1] == 300.0
    assert (df["net_worth"] == 0.0).all()


# --- account changes and eras ----------------------------------------------

def test_refinance_change_alters_rate_and_keeps_balance():
    p = build(end="2026-12", debts=[{
        "id": "m", "label": "m", "opening_balance": "10000", "apr": "0.12", "minimum_payment": "100",
        "changes": [{"month": "2026-07", "apr": "0.06"}]}])
    df = sim(p)
    june_bal = df["debt__m"][5]
    assert df["interest"][6] == float(q(Decimal(str(june_bal)) * Decimal("0.06") / 12))


def test_asset_contribution_change_takes_effect_at_its_month():
    p = build(end="2026-06", assets=[{
        "id": "sav", "label": "sav", "opening_balance": "0", "annual_return": "0",
        "monthly_contribution": "100",
        "changes": [{"month": "2026-04", "monthly_contribution": "0"}]}])
    df = sim(p)
    assert df["asset__sav"][2] == 300.0
    assert df["asset__sav"][5] == 300.0


def test_era_boundary_recorded_in_era_id():
    eras = [
        {"id": "a", "label": "a", "start": "2026-01", "end": "2026-02"},
        {"id": "b", "label": "b", "start": "2026-03", "end": "2026-04"},
    ]
    df = sim(build(eras=eras))
    assert df["era_id"].to_list() == ["a", "a", "b", "b"]


def test_eras_do_not_change_numbers():
    base = build(end="2026-04", incomes=[{"id": "s", "label": "s", "gross_annual": "12000"}])
    split = build(end="2026-04", incomes=[{"id": "s", "label": "s", "gross_annual": "12000"}],
                  eras=[{"id": "a", "label": "a", "start": "2026-01", "end": "2026-02"},
                        {"id": "b", "label": "b", "start": "2026-03", "end": "2026-04"}])
    a = sim(base).drop("era_id")
    b = sim(split).drop("era_id")
    assert a.equals(b)


def test_income_raise_applies_each_year_of_era():
    p = build(end="2027-12", incomes=[
        {"id": "s", "label": "s", "gross_annual": "12000", "annual_raise": "0.10"}])
    df = sim(p)
    assert df["income_gross"][0] == 1000.0
    assert df["income_gross"][12] == 1100.0


def test_tax_hook_is_used():
    from fpb.tax.base import FlatEffectiveTax

    p = build(end="2026-01", incomes=[{"id": "s", "label": "s", "gross_annual": "120000"}])
    df = simulate(p, horizon_end="2026-01", tax_model=FlatEffectiveTax(Decimal("0.25")))
    assert df["tax"][0] == 2500.0
    assert df["income_net"][0] == 7500.0


# --- inflation ------------------------------------------------------------

def test_inflation_linked_expense_grows_with_index():
    p = build(end="2027-01", expenses=[
        {"id": "g", "label": "g", "category": "food", "monthly": "1000"}], inflation="0.03")
    df = simulate(p, tax_model=NoTax())
    assert df["exp__food"][0] == 1000.0
    assert df["exp__food"][12] == 1030.0


def test_non_linked_expense_is_flat():
    p = build(end="2027-01", expenses=[
        {"id": "g", "label": "g", "category": "food", "monthly": "1000", "inflation_linked": False}],
        inflation="0.03")
    df = simulate(p, tax_model=NoTax())
    assert df["exp__food"][12] == 1000.0
