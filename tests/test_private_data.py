import json
from pathlib import Path

import pytest

from fpb.storage import load_plan, plan_from_json, plan_to_json

EXAMPLE = Path(__file__).resolve().parent.parent / "plans" / "example.yaml"


def test_json_round_trip_is_lossless():
    plan = load_plan(EXAMPLE)
    assert plan_from_json(plan_to_json(plan)) == plan


def test_json_is_plain_numbers_and_strings():
    data = json.loads(plan_to_json(load_plan(EXAMPLE)))
    assert data["debts"][0]["apr"] == 0.0625


def test_invalid_json_gives_readable_error():
    with pytest.raises(ValueError, match="not valid JSON"):
        plan_from_json("{not json")


def test_non_object_json_rejected():
    with pytest.raises(ValueError, match="JSON object"):
        plan_from_json("[1, 2]")


def test_invalid_plan_json_rejected_by_validation():
    data = json.loads(plan_to_json(load_plan(EXAMPLE)))
    data["incomes"][0]["end"] = "2000-01"
    with pytest.raises(ValueError):
        plan_from_json(json.dumps(data))
