# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pydantic>=2.6",
#     "pyyaml>=6.0",
#     "polars>=0.20",
#     "plotly>=5.18",
#     "anywidget>=0.9",
#     "marimo>=0.8",
# ]
# ///
import marimo

__generated_with = "0.25.1"
app = marimo.App(width="full", app_title="Finance Plan Builder")


@app.cell
def _():
    import marimo as mo
    from pathlib import Path

    from fpb._embedded import EXAMPLE_PLAN
    from fpb.actuals import coverage
    from fpb.deflator import view
    from fpb.engine import simulate
    from fpb.metrics import annual, summary
    from fpb.storage import load_plan, load_plan_text, plan_from_dict, plan_to_dict, save_plan
    from fpb.ui import PlanTimeline
    from fpb.viz import plots

    # Local runs read and write plans/. The WebAssembly export has no plans folder,
    # so it uses the bundled example and cannot save (read-only).
    try:
        PLAN_DIR = Path(__file__).resolve().parent / "plans"
    except NameError:
        PLAN_DIR = Path("plans")
    EDITABLE = PLAN_DIR.is_dir()
    return (
        EDITABLE,
        EXAMPLE_PLAN,
        PLAN_DIR,
        PlanTimeline,
        annual,
        coverage,
        load_plan,
        load_plan_text,
        mo,
        plan_from_dict,
        plan_to_dict,
        plots,
        save_plan,
        simulate,
        summary,
        view,
    )


@app.cell
def _(EDITABLE, PLAN_DIR, mo):
    plan_files = sorted(p.name for p in PLAN_DIR.glob("*.yaml")) if EDITABLE else ["example.yaml"]
    plan_picker = mo.ui.dropdown(options=plan_files, value=plan_files[0], label="Plan")
    horizon_year = mo.ui.slider(start=2030, stop=2080, step=1, value=2050, label="Horizon (year)")
    real_toggle = mo.ui.switch(value=False, label="Real (inflation-adjusted) dollars")
    reset_button = mo.ui.button(label="Reset to saved file")
    mo.hstack([plan_picker, horizon_year, real_toggle, reset_button], justify="start", gap=2)
    return horizon_year, plan_picker, real_toggle, reset_button


@app.cell
def _(
    EDITABLE,
    EXAMPLE_PLAN,
    PLAN_DIR,
    PlanTimeline,
    load_plan,
    load_plan_text,
    mo,
    plan_picker,
    plan_to_dict,
    reset_button,
):
    # Re-creating the widget resets it to the file on disk (picker change or reset).
    _ = reset_button.value
    if EDITABLE:
        _start = plan_to_dict(load_plan(PLAN_DIR / plan_picker.value))
    else:
        _start = plan_to_dict(load_plan_text(EXAMPLE_PLAN))
    timeline = mo.ui.anywidget(PlanTimeline(plan=_start))
    timeline
    return (timeline,)


@app.cell
def _(mo, plan_from_dict, timeline):
    # The browser sends the whole plan. Stop the outputs while it is invalid.
    _error = None
    try:
        plan = plan_from_dict(timeline.value["plan"])
    except Exception as exc:
        _error = exc
    mo.stop(
        _error is not None,
        mo.callout(mo.md(f"**Plan is invalid, outputs paused.** {_error}"), kind="danger"),
    )
    return (plan,)


@app.cell
def _(EDITABLE, PLAN_DIR, mo, plan, plan_picker, save_plan):
    _path = PLAN_DIR / plan_picker.value
    save_button = mo.ui.button(
        label="Save plan to YAML",
        kind="success",
        on_click=lambda _: save_plan(plan, _path),
    )
    _note = mo.md("") if EDITABLE else mo.md(
        "*Read-only copy. Changes are not saved. Run the app locally to save plans.*"
    )
    mo.hstack([save_button, _note]) if EDITABLE else _note
    return


@app.cell
def _(horizon_year, plan, simulate):
    horizon_end = f"{horizon_year.value}-12"
    if plan.end is not None and horizon_end > plan.end:
        horizon_end = plan.end
    nominal = simulate(plan, horizon_end=horizon_end)
    return (nominal,)


@app.cell
def _(coverage, mo, nominal, plan, real_toggle, summary, view):
    df = view(nominal, real=real_toggle.value)
    s = summary(df, plan)
    _through = coverage(plan)
    _note = (
        f"**Actual balances through {_through}.** Later months are projections."
        if _through
        else "**No statement balances loaded.** Every month is a projection. "
        "Run `python -m fpb.actuals template` to start populating past balances."
    )

    def card(label, value):
        return mo.stat(value=value, label=label)

    def money(x):
        return "n/a" if x is None else f"${x:,.0f}"

    cards = mo.hstack(
        [
            card("Net worth at horizon", money(s["net_worth_end"])),
            card("Debt-free", s["debt_free_month"] or "not within horizon"),
            card("Lowest cash", money(s["min_cash"])),
            card("First funding gap", s["first_deficit_month"] or "none"),
            card("Peak net worth", money(s["peak_net_worth"])),
        ],
        justify="start",
        gap=2,
        wrap=True,
    )
    mo.vstack([mo.md(_note), cards])
    return (df,)


@app.cell
def _(df, mo, plan, plots, real_toggle):
    figs = plots.dashboard(df, plan, real=real_toggle.value)
    tabs = mo.ui.tabs({name: mo.ui.plotly(fig) for name, fig in figs.items()})
    tabs
    return


@app.cell
def _(annual, df, mo):
    table = annual(df).select(
        ["year", "income_net", "expenses", "savings_rate", "net_worth_end", "debts_end", "assets_end", "any_deficit"]
    )
    mo.vstack([mo.md("### Annual summary"), mo.ui.table(table, selection=None, page_size=20)])
    return


if __name__ == "__main__":
    app.run()
