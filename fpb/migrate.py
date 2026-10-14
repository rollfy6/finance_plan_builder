"""Upgrade plan files from schema v1 to v2.

In v1, eras owned their incomes, expenses, debts, assets, events, debt strategy,
and assumptions. In v2 each of those is a top-level module with its own window,
and eras are labels only. The conversion keeps the calculated numbers the same:

* An era's incomes and expenses get the era's window.
* An era's debt strategy and assumptions become strategy and assumption windows
  with the era's window.
* A v1 account listing with an opening balance becomes an account with ``start``
  set to the era start. A later listing without an opening balance becomes a
  ``changes`` entry at the era start.
* Incomes, expenses, and events that reuse an id across eras are renamed with
  the era id as a suffix, because ids must be unique in v2.

Known limits: a v1 override that changes an account's ``owner``, ``account_type``,
or ``label`` is rejected or dropped, since v2 changes only carry rate and
contribution fields.
"""

from __future__ import annotations

import copy
from typing import Any

from fpb.models import SCHEMA_VERSION

_DEBT_CHANGE_FIELDS = ("apr", "minimum_payment", "convention")
_ASSET_CHANGE_FIELDS = ("annual_return", "monthly_contribution")


class MigrationError(ValueError):
    """A v1 plan cannot be expressed in schema v2 without changing its meaning."""


def migrate_v1(data: dict[str, Any]) -> dict[str, Any]:
    """Return a schema v2 dict equivalent to the v1 dict ``data``."""
    eras_v1 = data.get("eras") or []
    if not eras_v1:
        raise MigrationError("v1 plan must contain at least one era")

    out: dict[str, Any] = {
        k: copy.deepcopy(data[k])
        for k in ("name", "currency", "start", "base_year", "inflation", "cash_return", "opening_cash", "persons")
        if k in data
    }
    out["schema_version"] = SCHEMA_VERSION

    claimed: dict[str, str] = {}

    def claim(kind: str, ident: str) -> None:
        if ident in claimed:
            raise MigrationError(f"duplicate id {ident!r} ({kind}; already used by {claimed[ident]})")
        claimed[ident] = kind

    for p in out.get("persons", []):
        claim("person", p["id"])

    eras: list[dict] = []
    strategies: list[dict] = []
    assumptions: list[dict] = []
    debts: list[dict] = []
    assets: list[dict] = []
    debt_by_id: dict[str, dict] = {}
    asset_by_id: dict[str, dict] = {}

    # Pass 1: eras, strategies, assumptions, and account openings and overrides.
    # Accounts are claimed here so no later module can take their ids.
    for era in eras_v1:
        window = _window(era)
        claim("era", era["id"])
        eras.append({"id": era["id"], "label": era.get("name", era["id"]), **window})

        strat: dict[str, Any] = {
            "id": f"{era['id']}_strategy",
            "label": f"{era.get('name', era['id'])}: debt strategy",
            **window,
            "debt_strategy": era.get("debt_strategy", "avalanche"),
            "surplus_to_debt": era.get("surplus_to_debt", 1),
        }
        if era.get("debt_priority"):
            strat["debt_priority"] = list(era["debt_priority"])
        claim("strategy", strat["id"])
        strategies.append(strat)

        overrides = era.get("assumptions") or {}
        if overrides:
            a_id = f"{era['id']}_assumptions"
            claim("assumption", a_id)
            assumptions.append({"id": a_id, "label": f"{era.get('name', era['id'])}: assumptions",
                                **window, **overrides})

        for d in era.get("debts", []):
            if d.get("opening_balance") is None:
                base = _override_target(debt_by_id, d["id"], era["id"], "debt")
                _check_same(base, d, "owner", d["id"])
                change = {"month": era["start"], **{f: d[f] for f in _DEBT_CHANGE_FIELDS if f in d}}
                base["changes"].append(change)
            else:
                claim("debt", d["id"])
                entry = copy.deepcopy(d)
                entry["start"] = era["start"]
                entry["changes"] = []
                debt_by_id[d["id"]] = entry
                debts.append(entry)

        for a in era.get("assets", []):
            if a.get("opening_balance") is None:
                base = _override_target(asset_by_id, a["id"], era["id"], "asset")
                _check_same(base, a, "owner", a["id"])
                _check_same(base, a, "account_type", a["id"])
                change = {"month": era["start"], **{f: a[f] for f in _ASSET_CHANGE_FIELDS if f in a}}
                base["changes"].append(change)
            else:
                claim("asset", a["id"])
                entry = copy.deepcopy(a)
                entry["start"] = era["start"]
                entry["changes"] = []
                asset_by_id[a["id"]] = entry
                assets.append(entry)

    # Pass 2: incomes, expenses, and events, renamed on collision.
    def unique(ident: str, era_id: str, kind: str) -> str:
        candidate = ident
        if candidate in claimed:
            candidate = f"{ident}_{era_id}"
        claim(kind, candidate)
        return candidate

    incomes: list[dict] = []
    expenses: list[dict] = []
    events: list[dict] = []
    for era in eras_v1:
        window = _window(era)
        for inc in era.get("incomes", []):
            entry = {**copy.deepcopy(inc), **window}
            entry["id"] = unique(inc["id"], era["id"], "income")
            incomes.append(entry)
        for exp in era.get("expenses", []):
            entry = {**copy.deepcopy(exp), **window}
            entry["id"] = unique(exp["id"], era["id"], "expense")
            expenses.append(entry)
        for ev in era.get("events", []):
            entry = copy.deepcopy(ev)
            entry["id"] = unique(ev["id"], era["id"], "event")
            events.append(entry)

    out["eras"] = eras
    out["strategies"] = strategies
    out["assumptions"] = assumptions
    out["incomes"] = incomes
    out["expenses"] = expenses
    out["debts"] = debts
    out["assets"] = assets
    out["events"] = events
    return out


def _window(era: dict) -> dict[str, Any]:
    w: dict[str, Any] = {"start": era["start"]}
    if era.get("end") is not None:
        w["end"] = era["end"]
    return w


def _override_target(by_id: dict[str, dict], ident: str, era_id: str, kind: str) -> dict:
    if ident not in by_id:
        raise MigrationError(f"{kind} {ident!r} in era {era_id!r} overrides an account never opened")
    return by_id[ident]


def _check_same(base: dict, override: dict, field: str, ident: str) -> None:
    if override.get(field, base.get(field)) != base.get(field):
        raise MigrationError(f"{ident!r}: v1 override changes {field}, which v2 changes cannot express")
