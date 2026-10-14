"""Interactive plan timeline (anywidget).

The browser owns the editing: it drags modules along the timeline and edits
their parameters, writing the whole plan back to the ``plan`` trait. Python owns
validation. Every change is checked against the schema, and ``error`` holds the
message when the plan is not valid. Callers should read ``plan`` through
``fpb.storage.plan_from_dict`` and stop when it raises.
"""

from __future__ import annotations

import pathlib
from typing import Any

import anywidget
import traitlets
from pydantic import ValidationError

from fpb.storage import plan_from_dict

_HERE = pathlib.Path(__file__).resolve().parent


def validate_plan_dict(data: dict[str, Any]) -> str:
    """Return "" if ``data`` is a valid schema v2 plan, else a readable message."""
    try:
        plan_from_dict(data)
    except ValidationError as exc:
        return "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'plan'}: {err['msg']}" for err in exc.errors()
        )
    except ValueError as exc:
        return str(exc)
    return ""


class PlanTimeline(anywidget.AnyWidget):
    _esm = _HERE / "timeline.js"
    _css = _HERE / "timeline.css"

    plan = traitlets.Dict().tag(sync=True)
    error = traitlets.Unicode("").tag(sync=True)

    def __init__(self, plan: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(plan=plan, **kwargs)
        self.error = validate_plan_dict(self.plan)
        self.observe(self._on_plan_change, names="plan")

    def _on_plan_change(self, change: Any) -> None:
        self.error = validate_plan_dict(change["new"])
