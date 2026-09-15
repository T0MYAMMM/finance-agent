"""Read-only answers built from doeedd's reports; numbers are never recomputed here."""

from __future__ import annotations

from datetime import date
from typing import Any

from .aliases import AliasStore, resolve
from .capture import Names
from .client import DoeeddClient
from .formatting import MONTH_ABBREVIATIONS, idr, idr_short, percent, short_date

KIND_ORDER = ("income", "need", "want", "saving")


def context(client: DoeeddClient, aliases: AliasStore, today: date) -> dict[str, Any]:
    """What the agent needs before interpreting a message: names, habits, today."""
    categories = client.categories()
    memory = aliases.merged()
    return {
        "status": "ok",
        "today": today.isoformat(),
        "categories": {
            kind: [c["name"] for c in categories if c["kind"] == kind] for kind in KIND_ORDER
        },
        "accounts": [a["name"] for a in client.accounts()],
        "default_account": memory.get("default_account"),
        "merchant_defaults": memory["merchant_defaults"],
        "ambiguous_words": sorted(
            word
            for section in ("category", "account")
            for word, value in memory[section].items()
            if value is None
        ),
    }


def home(client: DoeeddClient, today: date) -> dict[str, Any]:
    """Payday countdown, this month's budget left, net worth."""
    data = client.report_home(today)
    month = data["month"]
    payday = data["payday"]
    month_name = MONTH_ABBREVIATIONS[today.month - 1]
    spent = idr_short(month["actual_expense"])
    if month["planned_expenses"]:
        budget = (
            f"💰 {month_name}: spent {spent} of {idr_short(month['planned_expenses'])} planned · "
            f"{idr_short(month['remaining'])} left"
        )
    else:
        budget = f"💰 {month_name}: spent {spent}; no budget planned for this month yet"
    lines = [
        budget,
        f"📅 Payday in {payday['days_until']} day(s) "
        f"({short_date(date.fromisoformat(payday['date']))})",
    ]
    net_worth = data["net_worth"]
    if net_worth["total"]:
        progress = net_worth["goal_progress"]
        goal = f" ({percent(progress)} of goal)" if progress is not None else ""
        lines.append(f"🏦 Net worth {idr_short(net_worth['total'])}{goal}")
    return {"status": "ok", "reply": "\n".join(lines), "data": data}


def monthly(
    client: DoeeddClient, year: int, month: int, category: str | None = None
) -> dict[str, Any]:
    """Planned versus actual; one category when asked, otherwise the busiest three."""
    data = client.report_monthly(year, month)
    rows = data["categories"]
    if category:
        match = resolve(category, [r["category"]["name"] for r in rows], {})
        row = next((r for r in rows if r["category"]["name"] == match.name), None)
        if row is None:
            return {
                "status": "needs_input",
                "missing": ["category"],
                "options": {"category": [r["category"]["name"] for r in rows]},
                "reply": f"No need/want category called {category!r} this month.",
            }
        return {"status": "ok", "reply": _category_line(row), "data": row}

    spent = sorted((r for r in rows if r["actual"]), key=lambda r: r["actual"], reverse=True)
    total = idr_short(data["actual_expense"])
    income = idr_short(data["actual_income"])
    if data["planned_expenses"]:
        header = (
            f"📊 {year}-{month:02d}: spent {spent} of {idr_short(data['planned_expenses'])} "
            f"planned · income {income}"
        )
    else:
        header = f"📊 {year}-{month:02d}: spent {spent}, no budget planned · income {income}"
    lines = [header]
    lines.extend(f"• {_category_line(r)}" for r in spent[:3])
    over = [r["category"]["name"] for r in rows if r["status"] == "over"]
    if over:
        lines.append(f"⚠️ Over budget: {', '.join(over)}")
    return {"status": "ok", "reply": "\n".join(lines), "data": data}


def _category_line(row: dict[str, Any]) -> str:
    name = row["category"]["name"]
    if not row["planned"]:
        return f"{name}: {idr(row['actual'])} spent (no budget planned)"
    left = row["planned"] - row["actual"]
    state = f"{idr(left)} left" if left >= 0 else f"over by {idr(-left)}"
    return (
        f"{name}: {idr(row['actual'])} of {idr(row['planned'])} ({percent(row['usage'])}), {state}"
    )


def summary(client: DoeeddClient, year: int, month: int) -> dict[str, Any]:
    """The month's plan by kind."""
    data = client.report_summary(year, month)
    kinds = ", ".join(
        f"{row['kind']} {idr_short(row['amount'])} ({percent(row['pct_of_income'])})"
        for row in data["by_kind"]
    )
    reply = (
        f"🗂️ Plan {year}-{month:02d}: income {idr_short(data['planned_income'])} · {kinds} · "
        f"unallocated {idr_short(data['non_allocated'])}"
    )
    return {"status": "ok", "reply": reply, "data": data}


def trend(client: DoeeddClient, start: str, end: str) -> dict[str, Any]:
    """Actual versus planned expenses month by month."""
    rows = client.report_trend(start, end)
    lines = [
        f"{row['month']}: {idr_short(row['actual_expense'])} "
        f"of {idr_short(row['planned_expenses'])}"
        for row in rows
    ]
    return {"status": "ok", "reply": "📈 " + "\n".join(lines), "data": rows}


def net_worth(client: DoeeddClient, at: date) -> dict[str, Any]:
    """Net worth with the liquid split."""
    data = client.net_worth(at)
    reply = (
        f"🏦 Net worth {idr_short(data['total'])} · liquid {idr_short(data['liquid'])} · "
        f"non-liquid {idr_short(data['non_liquid'])}"
    )
    return {"status": "ok", "reply": reply, "data": data}


def goals(client: DoeeddClient, at: date) -> dict[str, Any]:
    """Progress of every goal."""
    items = client.goals(at)
    if not items:
        return {"status": "ok", "reply": "No goals set yet.", "data": []}
    lines = [
        f"🎯 {g['name']}: {idr_short(g['current_amount'])} of {idr_short(g['target_amount'])} "
        f"({percent(g['progress'])})" + (f" by {g['target_date']}" if g.get("target_date") else "")
        for g in items
    ]
    return {"status": "ok", "reply": "\n".join(lines), "data": items}


def find(
    client: DoeeddClient,
    names: Names,
    filters: dict[str, Any],
    *,
    category: str | None = None,
    account: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Transactions matching the filters; the total is labelled as a sum of the listed rows."""
    query = dict(filters)
    for text, items, key in (
        (category, names.categories, "category_id"),
        (account, names.accounts, "account_id"),
    ):
        if not text:
            continue
        match = resolve(text, [item["name"] for item in items], {})
        found = next((item for item in items if item["name"] == match.name), None)
        if found is None:
            return {
                "status": "needs_input",
                "missing": [key.removesuffix("_id")],
                "options": {key.removesuffix("_id"): [item["name"] for item in items]},
                "reply": f"Unknown {key.removesuffix('_id')} {text!r}.",
            }
        query[key] = found["id"]
    rows = client.transactions(query, max_items=limit)
    items = [names.compact(row) for row in rows]
    total = sum(item["amount"] for item in items if item["type"] == "expense")
    lines = [
        f"• {item['date']} {idr(item['amount'])} {item['category'] or item['to_account'] or ''} "
        f"{item['merchant'] or item['description'] or ''}".rstrip()
        for item in items
    ]
    header = f"🔎 {len(items)} transaction(s)"
    if total:
        header += f"; expenses in this list sum to {idr(total)}"
    return {
        "status": "ok",
        "reply": "\n".join([header, *lines]),
        "items": items,
        "listed_expense_total": total,
    }


def review(client: DoeeddClient, names: Names, limit: int = 30) -> dict[str, Any]:
    """Entries nobody has looked at yet, newest first, numbered for a quick reply."""
    items = [
        names.compact(row) for row in client.transactions({"reviewed": False}, max_items=limit)
    ]
    if not items:
        return {"status": "ok", "reply": "✅ Nothing to review.", "items": []}
    noun = "entry" if len(items) == 1 else "entries"
    lines = [f"🧾 {len(items)} {noun} to review:"]
    lines.extend(f"{number}. {row_line(item)}" for number, item in enumerate(items, start=1))
    lines.append('Reply "approve all", or tell me which numbers to fix.')
    return {"status": "ok", "reply": "\n".join(lines), "items": items}


def receipts(client: DoeeddClient, names: Names, transaction: dict[str, Any]) -> dict[str, Any]:
    """Receipt links of one transaction."""
    items = client.attachments(transaction["id"])
    line = row_line(names.compact(transaction))
    if not items:
        return {"status": "ok", "reply": f"No receipt linked to {line}.", "items": []}
    links = [f"• {item.get('filename') or 'receipt'}: {item['url']}" for item in items]
    return {"status": "ok", "reply": "\n".join([f"📎 {line}", *links]), "items": items}


def row_line(item: dict[str, Any]) -> str:
    """``2026-09-14 Rp10.000 Food Tomoro`` or ``2026-09-13 Rp500.000 BCA → GoPay Top-up``."""
    if item["type"] == "transfer":
        where = f"{item['account']} → {item['to_account']}"
    else:
        where = item["category"] or ""
    label = item.get("merchant") or item.get("description") or ""
    return " ".join(part for part in (item["date"], idr(item["amount"]), where, label) if part)
