from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from fpb.deflator import price_index, to_real, view
from fpb.engine import simulate
from fpb.models import Plan
from fpb.storage import load_plan
from tests.helpers import NoTax, build

EXAMPLE = Path(__file__).resolve().parent.parent / "plans" / "example.yaml"


def test_index_is_one_at_base_january():
    p = build(end="2027-12", inflation="0.03", base_year=2026)
    idx = price_index(p, "2027-12")
    assert idx["2026-01"] == Decimal("1")


def test_index_compounds_to_annual_rate():
    p = build(end="2027-12", inflation="0.03", base_year=2026)
    idx = price_index(p, "2027-12")
    assert float(idx["2027-01"]) == pytest.approx(1.03, rel=1e-9)


def test_index_before_base_year_is_deflated_backwards():
    p = build(start="2025-01", end="2026-12", inflation="0.03", base_year=2026)
    idx = price_index(p, "2026-12")
    assert float(idx["2025-12"]) == pytest.approx(1 / 1.03 ** (1 / 12), rel=1e-9)
    assert float(idx["2026-01"]) == pytest.approx(1.0)
    assert float(idx["2025-01"]) == pytest.approx(1 / 1.03, rel=1e-9)


def test_assumption_window_inflation_drives_index():
    p = build(end="2027-12",
              assumptions=[{"id": "flat", "label": "flat", "start": "2026-01", "end": "2026-12", "inflation": "0.0"},
                           {"id": "hot", "label": "hot", "start": "2027-01", "end": "2027-12", "inflation": "0.10"}],
              inflation="0.03", base_year=2026)
    idx = price_index(p, "2027-12")
    # 2026 is flat at 0%; 2027 runs at 10%, so Dec 2027 is 11 monthly steps into the 10% year.
    assert float(idx["2027-01"]) == pytest.approx(1.0, rel=1e-9)
    assert float(idx["2027-12"]) == pytest.approx(1.10 ** (11 / 12), rel=1e-9)


def test_real_equals_nominal_in_base_january():
    p = build(end="2026-12", expenses=[
        {"id": "g", "label": "g", "category": "food", "monthly": "1000", "inflation_linked": False}],
        inflation="0.03", base_year=2026)
    df = simulate(p, tax_model=NoTax())
    real = to_real(df)
    assert real["exp__food"][0] == df["exp__food"][0]


def test_real_is_nominal_divided_by_index_and_nominal_untouched():
    p = build(end="2027-12", expenses=[
        {"id": "g", "label": "g", "category": "food", "monthly": "1000", "inflation_linked": False}],
        inflation="0.03", base_year=2026)
    df = simulate(p, tax_model=NoTax())
    snapshot = df.clone()
    real = view(df, real=True)
    assert df.equals(snapshot)  # nominal not mutated
    for i in (0, 11, 23):
        assert real["net_worth"][i] == pytest.approx(df["net_worth"][i] / df["price_index"][i], rel=1e-12)
    # A flat 1000 nominal expense is worth less each year in real terms.
    assert real["exp__food"][23] < real["exp__food"][0]


def test_assumption_window_ends_and_plan_default_resumes():
    p = build(end="2027-12", assumptions=[
        {"id": "hot", "label": "hot", "start": "2027-01", "end": "2027-06", "inflation": "0.10"}],
        inflation="0.03", base_year=2026)
    idx = price_index(p, "2027-12")
    # idx[m+1] / idx[m] uses the rate in force in month m.
    assert float(idx["2027-07"]) / float(idx["2027-06"]) == pytest.approx(1.10 ** (1 / 12), rel=1e-9)
    assert float(idx["2027-08"]) / float(idx["2027-07"]) == pytest.approx(1.03 ** (1 / 12), rel=1e-9)


def test_price_index_column_not_deflated():
    df = simulate(load_plan(EXAMPLE), horizon_end="2030-12")
    real = to_real(df)
    assert real["price_index"].to_list() == df["price_index"].to_list()


def test_view_nominal_returns_same_frame():
    df = simulate(load_plan(EXAMPLE), horizon_end="2027-12")
    assert view(df, real=False) is df
