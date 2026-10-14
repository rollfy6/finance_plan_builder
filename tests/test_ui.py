import copy
from pathlib import Path

from fpb.storage import load_plan, plan_to_dict
from fpb.ui import PlanTimeline, validate_plan_dict

EXAMPLE = Path(__file__).resolve().parent.parent / "plans" / "example.yaml"
JS = Path(__file__).resolve().parent.parent / "fpb" / "ui" / "timeline.js"


def _example_dict():
    return plan_to_dict(load_plan(EXAMPLE))


def test_valid_plan_has_no_error():
    assert validate_plan_dict(_example_dict()) == ""
    assert PlanTimeline(plan=_example_dict()).error == ""


def test_invalid_edit_sets_error_and_revert_clears_it():
    good = _example_dict()
    w = PlanTimeline(plan=good)
    bad = copy.deepcopy(good)
    bad["incomes"][0]["end"] = "2022-01"  # before its start (2023-06)
    w.plan = bad
    assert "before start" in w.error
    w.plan = good
    assert w.error == ""


def test_error_names_the_field_path():
    bad = _example_dict()
    bad["expenses"][0]["monthly"] = -5
    msg = validate_plan_dict(bad)
    assert msg.startswith("expenses.0.monthly")


def test_front_end_module_exports_render():
    text = JS.read_text()
    assert "export function render" in text
    assert "export default { render }" in text
