"""Budget plans by chat: show a month's plan, set lines, move planned money between categories.

A plan is what the owner intends to spend, so the skill always shows the change and asks before
calling the writes here. Only the lines being changed are sent, and each keeps its funding
account: doeedd clears a funding account that is left out of an update.
"""

from __future__ import annotations

from typing import Any

from .aliases import resolve
from .capture import Names
from .client import DoeeddClient
from .formatting import idr, idr_short, percent

KIND_TITLES = (("income", "Income"), ("saving", "Savings"), ("need", "Needs"), ("want", "Wants"))


def show_plan(client: DoeeddClient, year: int, month: int) -> dict[str, Any]:
    """The month's planned amounts grouped by kind, with allocation."""
    label = f"{year}-{month:02d}"
    period = client.budget_period(year, month)
    planned = [line for line in (period or {}).get("lines", []) if line["planned_amount"]]
    if period is None or not planned:
        return {"status": "ok", "reply": f"No budget planned for {label} yet.", "data": period}
    lines = [f"🗂️ Plan {label}:"]
    for kind, title in KIND_TITLES:
        items = [line for line in planned if line["category"]["kind"] == kind]
        if items:
            amounts = ", ".join(
                f"{line['category']['name']} {idr_short(line['planned_amount'])}" for line in items
            )
            lines.append(f"{title}: {amounts}")
    lines.append(_allocation(period["totals"]))
    return {"status": "ok", "reply": "\n".join(lines), "data": period}


def set_budget(
    client: DoeeddClient,
    names: Names,
    aliases: dict[str, Any],
    year: int,
    month: int,
    changes: list[tuple[str, int]],
) -> dict[str, Any]:
    """Set planned amounts for one or more categories in a month."""
    resolved: list[tuple[dict[str, Any], int]] = []
    unknown: list[str] = []
    for text, amount in changes:
        category = _category(names, aliases, text)
        if category is None:
            unknown.append(text)
        else:
            resolved.append((category, amount))
    if unknown:
        return _unknown_categories(names, unknown)

    funding = _funding_accounts(client.budget_period(year, month))
    body = [
        {
            "category_id": category["id"],
            "planned_amount": amount,
            "funding_account_id": funding.get(category["id"]),
        }
        for category, amount in resolved
    ]
    updated = client.put_budget_lines(year, month, body)
    changed = ", ".join(f"{category['name']} {idr_short(amount)}" for category, amount in resolved)
    reply = f"🗂️ {year}-{month:02d} plan updated: {changed}. {_allocation(updated['totals'])}."
    if updated["totals"]["non_allocated"] < 0:
        reply += " ⚠️ More is planned than the planned income."
    return {"status": "updated", "reply": reply, "data": updated}


def move_budget(
    client: DoeeddClient,
    names: Names,
    aliases: dict[str, Any],
    year: int,
    month: int,
    source_text: str,
    target_text: str,
    amount: int,
) -> dict[str, Any]:
    """Move planned money from one category to another within a month."""
    source = _category(names, aliases, source_text)
    target = _category(names, aliases, target_text)
    unknown = [
        text for text, item in ((source_text, source), (target_text, target)) if item is None
    ]
    if unknown or source is None or target is None:
        return _unknown_categories(names, unknown)
    if source["id"] == target["id"]:
        return {
            "status": "needs_input",
            "missing": ["to"],
            "reply": "Pick two different categories.",
        }

    period = client.budget_period(year, month)
    planned = {
        line["category_id"]: line["planned_amount"] for line in (period or {}).get("lines", [])
    }
    available = planned.get(source["id"], 0)
    if amount > available:
        return {
            "status": "needs_input",
            "missing": ["amount"],
            "reply": f"{source['name']} only has {idr(available)} planned for {year}-{month:02d}.",
        }
    funding = _funding_accounts(period)
    body = [
        {
            "category_id": source["id"],
            "planned_amount": available - amount,
            "funding_account_id": funding.get(source["id"]),
        },
        {
            "category_id": target["id"],
            "planned_amount": planned.get(target["id"], 0) + amount,
            "funding_account_id": funding.get(target["id"]),
        },
    ]
    updated = client.put_budget_lines(year, month, body)
    reply = (
        f"🔀 Moved {idr(amount)} from {source['name']} to {target['name']} for {year}-{month:02d} "
        f"({source['name']} {idr_short(available - amount)}, "
        f"{target['name']} {idr_short(planned.get(target['id'], 0) + amount)})."
    )
    return {"status": "updated", "reply": reply, "data": updated}


def _category(names: Names, aliases: dict[str, Any], text: str) -> dict[str, Any] | None:
    match = resolve(text, [item["name"] for item in names.categories], aliases)
    return next((item for item in names.categories if item["name"] == match.name), None)


def _unknown_categories(names: Names, unknown: list[str]) -> dict[str, Any]:
    return {
        "status": "needs_input",
        "missing": ["category"],
        "options": {"category": [item["name"] for item in names.categories]},
        "reply": f"Unknown category: {', '.join(unknown)}.",
    }


def _funding_accounts(period: dict[str, Any] | None) -> dict[str, str | None]:
    return {
        line["category_id"]: line["funding_account_id"] for line in (period or {}).get("lines", [])
    }


def _allocation(totals: dict[str, Any]) -> str:
    return (
        f"Allocated {percent(totals['allocated_pct'])} of income · "
        f"unallocated {idr_short(totals['non_allocated'])}"
    )
