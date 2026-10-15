"""Interactive plan timeline (anywidget).

The browser owns the editing: it drags modules along the timeline and edits
their parameters, writing the whole plan back to the ``plan`` trait. Python owns
validation. Every change is checked against the schema, and ``error`` holds the
message when the plan is not valid. Callers should read ``plan`` through
``fpb.storage.plan_from_dict`` and stop when it raises.
"""

from __future__ import annotations

from typing import Any

import anywidget
import traitlets
from pydantic import ValidationError

from fpb._embedded import TIMELINE_CSS, TIMELINE_ESM
from fpb.storage import plan_from_dict


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
    # Bundled as strings (see scripts/embed_assets.py) so the widget also works in
    # the WebAssembly export, which does not ship JS or CSS files.
    _esm = TIMELINE_ESM
    _css = TIMELINE_CSS

    plan = traitlets.Dict().tag(sync=True)
    error = traitlets.Unicode("").tag(sync=True)

    def __init__(self, plan: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(plan=plan, **kwargs)
        self.error = validate_plan_dict(self.plan)
        self.observe(self._on_plan_change, names="plan")

    def _on_plan_change(self, change: Any) -> None:
        self.error = validate_plan_dict(change["new"])
