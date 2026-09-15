from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from doeedd_agent.capture import Names
from doeedd_agent.holdings import create_asset, list_assets, record_valuation
from doeedd_agent.notify import commit_notification, in_quiet_hours, plan_notification
from doeedd_agent.notify_state import NotifyState


def category_row(name: str, planned: int, actual: int, status: str | None) -> dict[str, Any]:
    usage = actual / planned if planned else None
    return {
        "category": {"id": f"c-{name}", "name": name},
        "kind": "need",
        "planned": planned,
        "actual": actual,
        "usage": usage,
        "status": status,
    }


class FakeDoeedd:
    def __init__(self) -> None:
        self.days_until_payday = 10
        self.categories: list[dict[str, Any]] = []
        self.previous_month = {"planned_expenses": 5_000_000, "actual_expense": 4_200_000}
        self.week: list[dict[str, Any]] = []
        self.unreviewed: list[dict[str, Any]] = []
        self.no_receipt: list[dict[str, Any]] = []
        self.asset_rows: list[dict[str, Any]] = []
        self.valuations: list[tuple[str, dict[str, Any]]] = []
        self.created_assets: list[dict[str, Any]] = []

    def report_home(self, today: date) -> dict[str, Any]:
        return {
            "payday": {"date": "2026-09-25", "days_until": self.days_until_payday},
            "month": {
                "actual_expense": 1_000_000,
                "planned_expenses": 3_000_000,
                "remaining": 2_000_000,
            },
        }

    def report_monthly(self, year: int, month: int) -> dict[str, Any]:
        if month == 8:
            return {**self.previous_month, "categories": []}
        return {
            "planned_expenses": 3_000_000,
            "actual_expense": 1_000_000,
            "categories": self.categories,
        }

    def transactions(self, filters: dict[str, Any], *, max_items: int | None = None):
        if filters.get("reviewed") is False:
            return self.unreviewed
        if filters.get("has_attachment") is False:
            return self.no_receipt
        return self.week

    def assets(self) -> list[dict[str, Any]]:
        return self.asset_rows

    def add_valuation(self, asset_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.valuations.append((asset_id, body))
        return body

    def create_asset(self, body: dict[str, Any]) -> dict[str, Any]:
        self.created_assets.append(body)
        return {"id": "asset-new", **body}


EVENING = datetime(2026, 9, 16, 19, 0)  # a Wednesday


@pytest.fixture
def state(tmp_path: Path) -> NotifyState:
    return NotifyState(tmp_path / "notify_state.json")


def test_quiet_hours() -> None:
    assert in_quiet_hours(datetime(2026, 9, 16, 22, 0).time())
    assert in_quiet_hours(datetime(2026, 9, 16, 6, 59).time())
    assert not in_quiet_hours(datetime(2026, 9, 16, 7, 0).time())


def test_silent_when_nothing_is_due(state: NotifyState) -> None:
    assert plan_notification(FakeDoeedd(), state, EVENING) is None  # type: ignore[arg-type]


def test_budget_alert_once_per_level_then_escalates(state: NotifyState) -> None:
    doeedd = FakeDoeedd()
    doeedd.categories = [
        category_row("Food", 1_000_000, 750_000, "warn"),
        category_row("Shopping", 500_000, 450_000, "warn"),
    ]
    first = plan_notification(doeedd, state, EVENING)  # type: ignore[arg-type]
    assert first is not None and first.kind == "budget_alert"
    assert first.message.startswith("⚠️ Shopping is at 90% of budget, 10 day(s) to payday.")
    assert "1 more category" in first.message
    commit_notification(state, first, EVENING.date())

    next_day = datetime(2026, 9, 17, 19, 0)
    assert plan_notification(doeedd, state, next_day) is None  # type: ignore[arg-type]

    doeedd.categories[0] = category_row("Food", 1_000_000, 1_040_000, "over")
    escalated = plan_notification(doeedd, state, next_day)  # type: ignore[arg-type]
    assert escalated is not None
    assert escalated.message == "⚠️ Food is over budget: Rp1.040.000 of Rp1.000.000."


def test_one_scheduled_message_per_day_and_snooze(state: NotifyState) -> None:
    doeedd = FakeDoeedd()
    doeedd.categories = [category_row("Food", 1_000_000, 1_200_000, "over")]
    note = plan_notification(doeedd, state, EVENING)  # type: ignore[arg-type]
    assert note is not None
    commit_notification(state, note, EVENING.date())

    doeedd.categories.append(category_row("Travel", 100_000, 150_000, "over"))
    assert plan_notification(doeedd, state, EVENING) is None  # type: ignore[arg-type]

    state.snooze(date(2026, 9, 20), EVENING.date())
    assert plan_notification(doeedd, state, datetime(2026, 9, 20, 19, 0)) is None  # type: ignore[arg-type]
    assert plan_notification(doeedd, state, datetime(2026, 9, 21, 19, 0)) is not None  # type: ignore[arg-type]


def test_payday_message_once_a_month(state: NotifyState) -> None:
    doeedd = FakeDoeedd()
    doeedd.days_until_payday = 0
    note = plan_notification(doeedd, state, datetime(2026, 9, 25, 19, 0))  # type: ignore[arg-type]
    assert note is not None and note.kind == "payday"
    assert note.message.splitlines() == [
        "💸 Payday!",
        "Aug: spent Rp4,2jt of Rp5jt planned.",
        "Want me to copy that budget plan to the coming month?",
    ]
    commit_notification(state, note, date(2026, 9, 25))
    assert plan_notification(doeedd, state, datetime(2026, 9, 26, 19, 0)) is None  # type: ignore[arg-type]


def test_sunday_digest_mentions_review_and_receipts(state: NotifyState) -> None:
    doeedd = FakeDoeedd()
    doeedd.week = [
        {"type": "expense", "amount": 27_600},
        {"type": "expense", "amount": 30_000},
        {"type": "transfer", "amount": 500_000},
    ]
    doeedd.unreviewed = [{"id": "t1"}]
    doeedd.no_receipt = [{"id": "t2"}]
    note = plan_notification(doeedd, state, datetime(2026, 9, 20, 19, 0))  # type: ignore[arg-type]
    assert note is not None and note.kind == "weekly_digest"
    assert note.message.splitlines() == [
        "🗓️ This week: 2 expense(s), together Rp57.600.",
        "Sep budget: Rp2jt left of Rp3jt.",
        '🧾 1 entry to review; reply "review".',
        "📎 1 expense(s) this week have no receipt.",
    ]


def test_balance_prompt_on_the_first_only_with_assets(state: NotifyState) -> None:
    doeedd = FakeDoeedd()
    first_of_month = datetime(2026, 10, 1, 19, 0)
    assert plan_notification(doeedd, state, first_of_month) is None  # type: ignore[arg-type]
    doeedd.asset_rows = [{"id": "a1", "name": "BCA"}, {"id": "a2", "name": "Reksadana"}]
    note = plan_notification(doeedd, state, first_of_month)  # type: ignore[arg-type]
    assert note is not None
    assert (
        note.message
        == '🏦 New month: quick balance update for BCA, Reksadana? Reply like "BCA 12,5jt".'
    )


def test_assets_list_valuation_and_creation() -> None:
    doeedd = FakeDoeedd()
    assert list_assets(doeedd)["reply"] == "No assets tracked yet."  # type: ignore[arg-type]

    doeedd.asset_rows = [
        {"id": "a1", "name": "BCA", "current_value": 11_000_000, "valued_on": "2026-09-01"},
    ]
    assert list_assets(doeedd)["reply"] == "🏦 Assets:\n• BCA: Rp11jt (1 Sep)"  # type: ignore[arg-type]

    result = record_valuation(doeedd, "bca", 12_500_000, date(2026, 9, 15))  # type: ignore[arg-type]
    assert result["reply"] == "🏦 BCA: Rp12,5jt on 15 Sep (was Rp11jt on 1 Sep)."
    assert doeedd.valuations == [("a1", {"value": 12_500_000, "valued_on": "2026-09-15"})]

    unknown = record_valuation(doeedd, "jenius", 1_000, date(2026, 9, 15))  # type: ignore[arg-type]
    assert unknown["status"] == "needs_input" and unknown["options"] == {"asset": ["BCA"]}

    names = Names(categories=[], accounts=[{"id": "acc-bca", "name": "BCA"}])
    created = create_asset(
        doeedd,
        names,
        "Reksadana",
        5_000_000,
        date(2026, 9, 15),
        account="bca",
        liquid=False,  # type: ignore[arg-type]
    )
    assert created["reply"] == "🏦 Tracking Reksadana (non-liquid): Rp5jt on 15 Sep."
    assert doeedd.created_assets[0] == {
        "name": "Reksadana",
        "is_liquid": False,
        "value": 5_000_000,
        "valued_on": "2026-09-15",
        "account_id": "acc-bca",
    }
