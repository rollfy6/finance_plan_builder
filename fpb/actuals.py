"""Populate past balances from bank and brokerage statements.

Workflow
--------
1. Write a template listing every open account at each quarter-end month::

       python -m fpb.actuals template plans/example.yaml balances.csv --through 2026-09

2. Fill in the ``balance`` column from statements. Blank rows are skipped, so
   partial fills are fine.

3. Import the filled CSV into the plan file::

       python -m fpb.actuals import plans/example.yaml balances.csv

Imported balances are stored as ``balance_history`` in the YAML. Months with a
balance use that value. Other months are projections.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Optional

from fpb.models import CASH_ACCOUNT, BalanceSnapshot, Plan, month_index
from fpb.storage import load_plan, plan_from_dict, plan_to_dict, save_plan

HEADER = ["month", "account", "label", "balance"]


def _open_accounts(plan: Plan, month: str) -> list[tuple[str, str]]:
    idx = month_index(month)
    out = [(CASH_ACCOUNT, "Cash")]
    for acct in (*plan.debts, *plan.assets):
        if month_index(acct.start) <= idx:
            out.append((acct.id, acct.label))
    return out


def quarter_end_months(plan: Plan, through: str) -> list[str]:
    """Quarter-end months (Mar, Jun, Sep, Dec) from plan start through ``through``."""
    return [m for m in plan.months(through) if month_index(m) % 3 == 2]


def write_template(plan: Plan, path: str | Path, through: str) -> int:
    """Write a CSV with a blank balance for each open account at each quarter end. Returns row count."""
    rows = 0
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        for month in quarter_end_months(plan, through):
            for account, label in _open_accounts(plan, month):
                w.writerow([month, account, label, ""])
                rows += 1
    return rows


def read_balances(path: str | Path) -> tuple[list[BalanceSnapshot], int]:
    """Read filled rows. Returns (snapshots, number of blank rows skipped).

    Raises ValueError with the line number for malformed rows.
    """
    snapshots: list[BalanceSnapshot] = []
    skipped = 0
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = {"month", "account", "balance"} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}: missing columns {sorted(missing)}")
        for line, row in enumerate(reader, start=2):
            raw = (row.get("balance") or "").strip()
            if raw == "":
                skipped += 1
                continue
            try:
                snapshots.append(
                    BalanceSnapshot(
                        month=(row["month"] or "").strip(),
                        account=(row["account"] or "").strip(),
                        balance=raw,
                    )
                )
            except ValueError as exc:
                raise ValueError(f"{path}, line {line}: {exc}") from exc
    return snapshots, skipped


def apply_balances(plan: Plan, snapshots: list[BalanceSnapshot]) -> Plan:
    """Return a new validated plan with ``snapshots`` merged into balance_history.

    A snapshot replaces an existing one for the same month and account.
    """
    data = plan_to_dict(plan)
    new_keys = {(s.month, s.account) for s in snapshots}
    kept = [h for h in data.get("balance_history", []) if (h["month"], h["account"]) not in new_keys]
    fresh = [{"month": s.month, "account": s.account, "balance": float(s.balance)} for s in snapshots]
    data["balance_history"] = kept + fresh
    data["balance_history"].sort(key=lambda h: (h["month"], h["account"]))
    return plan_from_dict(data)


def coverage(plan: Plan) -> Optional[str]:
    """Last month with actual balances, or None if the plan has no actuals."""
    if not plan.balance_history:
        return None
    return max(s.month for s in plan.balance_history)


def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fpb.actuals")
    sub = parser.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("template", help="write a CSV of blank statement balances")
    t.add_argument("plan")
    t.add_argument("out")
    t.add_argument("--through", required=True, help="last statement month, YYYY-MM")

    i = sub.add_parser("import", help="merge filled balances into the plan file")
    i.add_argument("plan")
    i.add_argument("csv")

    args = parser.parse_args(argv)
    plan = load_plan(args.plan)
    if args.cmd == "template":
        n = write_template(plan, args.out, args.through)
        print(f"wrote {n} rows to {args.out}")
        return 0
    snapshots, skipped = read_balances(args.csv)
    updated = apply_balances(plan, snapshots)
    save_plan(updated, args.plan)
    print(f"imported {len(snapshots)} balances ({skipped} blank rows skipped); "
          f"actuals through {coverage(updated)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
