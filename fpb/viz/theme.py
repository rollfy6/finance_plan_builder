"""Colors and layout defaults shared by all figures."""

from __future__ import annotations

from fpb.models import Plan

PALETTE = [
    "#4C78A8", "#F58518", "#54A24B", "#E45756", "#72B7B2",
    "#B279A2", "#FF9DA6", "#9D755D", "#BAB0AC", "#EECA3B",
]

ERA_BAND_OPACITY = 0.08


def era_colors(plan: Plan) -> dict[str, str]:
    return {e.id: PALETTE[i % len(PALETTE)] for i, e in enumerate(plan.eras)}


def series_colors(names: list[str]) -> dict[str, str]:
    return {n: PALETTE[i % len(PALETTE)] for i, n in enumerate(names)}


LAYOUT = dict(
    template="plotly_white",
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    margin=dict(l=60, r=20, t=80, b=40),
    height=420,
)
