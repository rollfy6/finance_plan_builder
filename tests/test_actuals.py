import csv
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest
from pydantic import ValidationError

from fpb.actuals import (
    apply_balances,
    coverage,
    quarter_end_months,
    read_balances,
    write_template,
    _main,
)
from fpb.models import BalanceSnapshot, Plan
from fpb.storage import load_plan, plan_to_dict, save_plan
from tests.helpers import build, sim

EXAMPLE = Path(__file__).resolve().parent.parent / "plans" / "example.yaml"


def _loan_plan(history=()):
    return build(end="2026-12", strategies=[{"id": "s", "label": "s", "surplus_to_debt": "0"}],
                 debts=[{"id": "loan", "label": "loan", "opening_balance": "1000", "apr": "0",
                         "minimum_payment": "100"}],
                 assets=[{"id": "fund", "label": "fund", "opening_balance": "500", "annual_return": "0"}],
                 balance_history=list(history))


# --- engine behaviour -------------------------------------------------------

def test_actual_balance_replaces_projection_in_its_month():
    df = sim(_loan_plan([{"month": "2026-03", "account": "loan", "balance": "700"}]))
    assert df["debt__loan"][2] == 700.0
    assert df["source"][2] == "actual"
    assert df["source"][1] == "projected"


def test_later_months_continue_from_the_actual():
    df = sim(_loan_plan([{"month": "2026-03", "account": "loan", "balance": "700"}]))
    assert df["debt__loan"][3] == 600.0  # 700 minus the 100 minimum


def test_unlisted_accounts_keep_their_projection():
    df = sim(_loan_plan([{"month": "2026-03", "account": "loan", "balance": "700"}]))
    assert df["asset__fund"][2] == 500.0


def test_cash_actual_sets_cash_balance():
    df = sim(_loan_plan([{"month": "2026-02", "account": "cash", "balance": "1234.56"}]))
    assert df["cash"][1] == 1234.56


def test_net_worth_identity_holds_with_actuals():
    p = _loan_plan([
        {"month": "2026-03", "account": "loan", "balance": "700"},
        {"month": "2026-06", "account": "fund", "balance": "9000"},
    ])
    df = sim(p)
    expected = df["cash"] + df["assets_total"] - df["debts_total"]
    assert (df["net_worth"] - expected).abs().max() < 1e-6


def test_example_past_months_are_marked_projected_without_actuals():
    df = simulate_example("2024-12")
    assert set(df["source"].to_list()) == {"projected"}


def simulate_example(end):
    from fpb.engine import simulate

    return simulate(load_plan(EXAMPLE), horizon_end=end)


# --- validation -------------------------------------------------------------

@pytest.mark.parametrize("snap, msg", [
    ({"month": "2026-03", "account": "nope", "balance": "1"}, "unknown account"),
    ({"month": "2025-03", "account": "loan", "balance": "1"}, "before plan start"),
    ({"month": "2027-03", "account": "loan", "balance": "1"}, "after plan end"),
    ({"month": "2026-03", "account": "loan", "balance": "-1"}, None),
])
def test_invalid_snapshots_rejected(snap, msg):
    with pytest.raises(ValidationError, match=msg) if msg else pytest.raises(ValidationError):
        _loan_plan([snap])


def test_duplicate_month_account_rejected():
    with pytest.raises(ValidationError, match="duplicate balance"):
        _loan_plan([{"month": "2026-03", "account": "loan", "balance": "1"},
                    {"month": "2026-03", "account": "loan", "balance": "2"}])


def test_snapshot_before_account_opens_rejected():
    p = build(end="2026-12", debts=[{"id": "late", "label": "late", "start": "2026-06",
                                     "opening_balance": "1000", "apr": "0", "minimum_payment": "10"}])
    data = plan_to_dict(p)
    data["balance_history"] = [{"month": "2026-03", "account": "late", "balance": 5}]
    with pytest.raises(ValidationError, match="before the account opens"):
        Plan.model_validate(data)


def test_cash_is_reserved_as_an_account_id():
    with pytest.raises(ValidationError, match="reserved"):
        build(end="2026-12", assets=[{"id": "cash", "label": "c", "opening_balance": "1",
                                      "annual_return": "0"}])


# --- populate from CSV ------------------------------------------------------

def test_template_lists_open_accounts_at_quarter_ends_only(tmp_path):
    plan = load_plan(EXAMPLE)
    out = tmp_path / "t.csv"
    write_template(plan, out, through="2024-06")
    rows = list(csv.DictReader(out.open()))
    months = {r["month"] for r in rows}
    assert months == {"2023-06", "2023-09", "2023-12", "2024-03", "2024-06"}
    assert all(r["balance"] == "" for r in rows)
    # Mortgage opens 2023-06, so it appears in the first quarter-end.
    assert any(r["account"] == "mortgage" and r["month"] == "2023-06" for r in rows)
    # College fund opens 2034, so it is absent before then.
    assert not any(r["account"] == "college_fund" for r in rows)


def test_quarter_end_months_are_march_june_september_december():
    plan = load_plan(EXAMPLE)
    assert quarter_end_months(plan, "2024-01") == ["2023-06", "2023-09", "2023-12"]


def test_blank_rows_skipped_and_filled_rows_read(tmp_path):
    f = tmp_path / "b.csv"
    f.write_text("month,account,label,balance\n2023-06,mortgage,Mortgage,379500.12\n2023-06,car_loan,Car,\n")
    snaps, skipped = read_balances(f)
    assert skipped == 1
    assert snaps[0].account == "mortgage" and snaps[0].balance == Decimal("379500.12")


def test_malformed_row_reports_line_number(tmp_path):
    f = tmp_path / "b.csv"
    f.write_text("month,account,label,balance\n2023-06,mortgage,M,abc\n")
    with pytest.raises(ValueError, match="line 2"):
        read_balances(f)


def test_apply_replaces_existing_snapshot_for_same_month_and_account():
    plan = _loan_plan([{"month": "2026-03", "account": "loan", "balance": "700"}])
    updated = apply_balances(plan, [BalanceSnapshot(month="2026-03", account="loan", balance="650")])
    assert [(s.month, s.balance) for s in updated.balance_history] == [("2026-03", Decimal("650"))]
    assert coverage(updated) == "2026-03"


def test_cli_import_writes_plan_file(tmp_path):
    src = tmp_path / "plan.yaml"
    save_plan(load_plan(EXAMPLE), src)
    csv_path = tmp_path / "b.csv"
    csv_path.write_text("month,account,label,balance\n2024-06,mortgage,Mortgage,375000\n")
    assert _main(["import", str(src), str(csv_path)]) == 0
    reloaded = load_plan(src)
    assert [(s.month, s.account) for s in reloaded.balance_history] == [("2024-06", "mortgage")]


def test_cli_template_round_trip(tmp_path):
    out = tmp_path / "t.csv"
    assert _main(["template", str(EXAMPLE), str(out), "--through", "2023-12"]) == 0
    assert out.exists()
