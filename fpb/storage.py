"""YAML <-> Plan persistence.

YAML is the on-disk source of truth. Decimal money is written as plain numbers
(not quoted strings) so plans stay readable, and read back through Decimal(str(x))
so no binary-float noise leaks in.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from fpb.migrate import migrate_v1
from fpb.models import Plan


def _to_plain(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        # Normalize e.g. Decimal('5000.00') -> 5000.0, Decimal('0.0525') -> 0.0525
        return float(obj)
    if isinstance(obj, dict):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_plain(v) for v in obj]
    if hasattr(obj, "value"):  # Enum members
        return obj.value
    return obj


def _from_plain(obj: Any) -> Any:
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _from_plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_from_plain(v) for v in obj]
    return obj


def plan_to_dict(plan: Plan) -> dict[str, Any]:
    # exclude_none keeps hand-edited files clean: open-ended windows omit end.
    # Both forms reload to the same None value.
    data = plan.model_dump(mode="python", exclude_none=True)
    return _to_plain(data)


def plan_from_dict(data: dict[str, Any]) -> Plan:
    """Build a Plan from a dict. Schema v1 dicts are migrated to v2 first."""
    if data.get("schema_version", 1) == 1:
        data = migrate_v1(data)
    return Plan.model_validate(_from_plain(data))


def load_plan_text(text: str) -> Plan:
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("top level must be a mapping")
    return plan_from_dict(data)


def load_plan(path: str | Path) -> Plan:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return plan_from_dict(data)


def save_plan(plan: Plan, path: str | Path) -> None:
    """Write atomically so a crash mid-save cannot corrupt the plan file."""
    target = Path(path)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        yaml.safe_dump(plan_to_dict(plan), fh, sort_keys=False, allow_unicode=True)
    tmp.replace(target)
