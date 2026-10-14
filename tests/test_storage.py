from decimal import Decimal
from pathlib import Path

from fpb.models import Plan
from fpb.storage import load_plan, plan_from_dict, plan_to_dict, save_plan

EXAMPLE = Path(__file__).resolve().parent.parent / "plans" / "example.yaml"


def test_example_plan_loads_and_is_valid():
    plan = load_plan(EXAMPLE)
    assert [e.id for e in plan.eras] == ["first_home", "mid_career", "family_peak"]
    assert plan.schema_version == 2
    assert plan.end is None  # final era open-ended
    assert plan.era_for_month("2029-06").id == "mid_career"


def test_round_trip_is_lossless(tmp_path):
    original = load_plan(EXAMPLE)
    out = tmp_path / "rt.yaml"
    save_plan(original, out)
    reloaded = load_plan(out)
    assert reloaded == original


def test_money_survives_round_trip_without_float_noise(tmp_path):
    original = load_plan(EXAMPLE)
    out = tmp_path / "rt.yaml"
    save_plan(original, out)
    reloaded = load_plan(out)
    rate = next(d for d in reloaded.debts if d.id == "credit_card").apr
    assert rate == Decimal("0.2499")
    assert str(rate) == "0.2499"  # not 0.24990000000000001


def test_saved_file_has_no_null_noise(tmp_path):
    out = tmp_path / "rt.yaml"
    save_plan(load_plan(EXAMPLE), out)
    text = out.read_text()
    assert "null" not in text
    assert "!!python" not in text  # enums and decimals serialize as plain values


def test_dict_round_trip_matches_model():
    plan = load_plan(EXAMPLE)
    assert plan_from_dict(plan_to_dict(plan)) == plan


def test_atomic_save_leaves_no_tmp_file(tmp_path):
    out = tmp_path / "plan.yaml"
    save_plan(load_plan(EXAMPLE), out)
    assert out.exists()
    assert not (tmp_path / "plan.yaml.tmp").exists()


def test_invalid_file_fails_validation(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("schema_version: 2\nname: x\nstart: 2026-01\nbase_year: 2026\neras: []\n")
    try:
        load_plan(bad)
    except Exception as exc:  # pydantic.ValidationError
        assert "at least one era" in str(exc)
    else:
        raise AssertionError("expected validation failure")
