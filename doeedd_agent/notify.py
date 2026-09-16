"""Proactive messages under a strict budget.

At most one scheduled message a day, nothing during quiet hours or a snooze, budget alerts once
per category and level per month, and silence (``[SILENT]``, which Hermes cron does not deliver)
when there is nothing worth saying. Priority: budget alert, payday, weekly digest, balance prompt.
Numbers come from doeedd reports; the weekly figure is labelled as a sum of the week's entries.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from .client import DoeeddClient
from .formatting import MONTH_ABBREVIATIONS, idr, idr_short, percent
from .notify_state import LEVELS, NotifyState

SILENT = "[SILENT]"
QUIET_START = time(21, 30)
QUIET_END = time(7, 0)
SUNDAY = 6


@dataclass
class Notification:
    """One message to deliver, with what to remember once it is sent."""

    kind: str
    message: str
    alerts: list[tuple[str, str]] = field(default_factory=list)


def in_quiet_hours(moment: time) -> bool:
    """21:30 to 07:00 in the owner's timezone."""
    return moment >= QUIET_START or moment < QUIET_END


def plan_notification(
    client: DoeeddClient, state: NotifyState, now: datetime
) -> Notification | None:
    """The single most useful message for now, or ``None`` to stay silent."""
    today = now.date()
    snoozed = state.snoozed_until()
    if snoozed is not None and today <= snoozed:
        return None
    if in_quiet_hours(now.time()) or state.sent_on(today):
        return None
    home = client.report_home(today)
    builders: tuple[Callable[..., Notification | None], ...] = (
        _budget_alert,
        _payday,
        _weekly_digest,
        _balance_prompt,
    )
    for build in builders:
        notification = build(client, state, today, home)
        if notification is not None:
            return notification
    return None


def commit_notification(state: NotifyState, notification: Notification, today: date) -> None:
    """Record a delivered message so it counts against the budget and is not repeated."""
    state.record_sent(today, notification.kind)
    for category, level in notification.alerts:
        state.mark_alerted(f"{today:%Y-%m}", category, level, today)


def _budget_alert(
    client: DoeeddClient, state: NotifyState, today: date, home: dict[str, Any]
) -> Notification | None:
    month = f"{today:%Y-%m}"
    report = client.report_monthly(today.year, today.month)
    fresh = []
    for row in report["categories"]:
        level = row["status"]
        if level not in LEVELS:
            continue
        already = state.alert_level(month, row["category"]["name"])
        if already is not None and LEVELS[already] >= LEVELS[level]:
            continue
        fresh.append(row)
    if not fresh:
        return None
    worst = max(fresh, key=lambda row: (LEVELS[row["status"]], row["usage"] or 0))
    name = worst["category"]["name"]
    if worst["status"] == "over":
        message = f"⚠️ {name} is over budget: {idr(worst['actual'])} of {idr(worst['planned'])}."
    else:
        days = home["payday"]["days_until"]
        message = f"⚠️ {name} is at {percent(worst['usage'])} of budget, {days} day(s) to payday."
    others = len(fresh) - 1
    if others:
        noun = "category" if others == 1 else "categories"
        message += f' {others} more {noun} close to the limit; reply "budget" for details.'
    alerts = [(row["category"]["name"], row["status"]) for row in fresh]
    return Notification("budget_alert", message, alerts)


def _payday(
    client: DoeeddClient, state: NotifyState, today: date, home: dict[str, Any]
) -> Notification | None:
    if home["payday"]["days_until"] != 0 or state.sent_in_month("payday", f"{today:%Y-%m}"):
        return None
    previous = today.replace(day=1) - timedelta(days=1)
    report = client.report_monthly(previous.year, previous.month)
    name = MONTH_ABBREVIATIONS[previous.month - 1]
    lines = ["💸 Payday!"]
    if report["planned_expenses"]:
        lines.append(
            f"{name}: spent {idr_short(report['actual_expense'])} of "
            f"{idr_short(report['planned_expenses'])} planned."
        )
        lines.append("Want me to copy that budget plan to the coming month?")
    else:
        lines.append(
            f"{name}: spent {idr_short(report['actual_expense'])} (no budget was planned)."
        )
    return Notification("payday", "\n".join(lines))


def _weekly_digest(
    client: DoeeddClient, state: NotifyState, today: date, home: dict[str, Any]
) -> Notification | None:
    if today.weekday() != SUNDAY:
        return None
    start = today - timedelta(days=6)
    week = client.transactions({"from": start, "to": today}, max_items=500)
    unreviewed = client.transactions({"reviewed": False}, max_items=500)
    if not week and not unreviewed:
        return None
    expenses = [row for row in week if row["type"] == "expense"]
    total = sum(row["amount"] for row in expenses)
    lines = [f"🗓️ This week: {len(expenses)} expense(s), together {idr(total)}."]
    month = home["month"]
    if month["planned_expenses"]:
        lines.append(
            f"{MONTH_ABBREVIATIONS[today.month - 1]} budget: {idr_short(month['remaining'])} left "
            f"of {idr_short(month['planned_expenses'])}."
        )
    if unreviewed:
        noun = "entry" if len(unreviewed) == 1 else "entries"
        lines.append(f'🧾 {len(unreviewed)} {noun} to review; reply "review".')
    missing = client.transactions(
        {"type": "expense", "has_attachment": False, "from": start, "to": today}, max_items=500
    )
    if missing:
        lines.append(f"📎 {len(missing)} expense(s) this week have no receipt.")
    return Notification("weekly_digest", "\n".join(lines))


def _balance_prompt(
    client: DoeeddClient, state: NotifyState, today: date, home: dict[str, Any]
) -> Notification | None:
    if today.day != 1:
        return None
    assets = client.assets()
    if not assets:
        return None
    names = ", ".join(asset["name"] for asset in assets[:6])
    example = assets[0]["name"]
    return Notification(
        "balance_prompt",
        f'🏦 New month: quick balance update for {names}? Reply like "{example} 12,5jt".',
    )
