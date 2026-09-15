from datetime import date
from typing import Any

from doeedd_agent import queries
from doeedd_agent.capture import Names

TODAY = date(2026, 9, 15)


class FakeReports:
    """The report endpoints queries.py reads, with fixed data."""

    def __init__(self, planned_expenses: int = 0) -> None:
        self.planned_expenses = planned_expenses
        self.filters: dict[str, Any] = {}

    def report_home(self, today: date) -> dict[str, Any]:
        return {
            "today": today.isoformat(),
            "payday": {"date": "2026-09-25", "days_until": 10},
            "month": {
                "actual_expense": 25_000,
                "planned_expenses": self.planned_expenses,
                "remaining": self.planned_expenses - 25_000,
            },
            "recent": [],
            "net_worth": {"total": 0, "goal_progress": None},
        }

    def report_monthly(self, year: int, month: int) -> dict[str, Any]:
        return {
            "planned_income": 0,
            "actual_income": 0,
            "planned_expenses": 1_000_000,
            "actual_expense": 1_150_000,
            "planned_savings": 0,
            "categories": [
                {
                    "category": {"id": "c-food", "name": "Food"},
                    "kind": "need",
                    "planned": 1_000_000,
                    "actual": 1_150_000,
                    "usage": 1.15,
                    "status": "over",
                },
                {
                    "category": {"id": "c-shop", "name": "Shopping"},
                    "kind": "want",
                    "planned": 0,
                    "actual": 0,
                    "usage": None,
                    "status": None,
                },
            ],
        }

    def transactions(self, filters: dict[str, Any], *, max_items: int | None = None):
        self.filters = filters
        base = {
            "type": "expense",
            "account_id": "a-bca",
            "category_id": "c-food",
            "transfer_to_account_id": None,
            "description": "kopi",
            "payment_method": None,
            "source": "agent",
            "is_reviewed": False,
            "deleted_at": None,
        }
        return [
            {
                **base,
                "id": "t1",
                "occurred_on": "2026-09-14",
                "amount": 10_000,
                "merchant": "Tomoro",
            },
            {**base, "id": "t2", "occurred_on": "2026-09-13", "amount": 20_000, "merchant": None},
        ]


def test_home_without_a_plan_does_not_report_a_negative_budget() -> None:
    reply = queries.home(FakeReports(0), TODAY)["reply"]  # type: ignore[arg-type]
    assert "💰 Sep: spent Rp25.000; no budget planned for this month yet" in reply
    assert "-Rp" not in reply
    assert "📅 Payday in 10 day(s) (25 Sep)" in reply


def test_home_with_a_plan_shows_what_is_left() -> None:
    reply = queries.home(FakeReports(2_000_000), TODAY)["reply"]  # type: ignore[arg-type]
    assert "spent Rp25.000 of Rp2jt planned" in reply
    assert "left" in reply


def test_monthly_single_category_overview_and_unknown_name() -> None:
    client = FakeReports()
    food = queries.monthly(client, 2026, 9, "food")  # type: ignore[arg-type]
    assert food["reply"] == "Food: Rp1.150.000 of Rp1.000.000 (115%), over by Rp150.000"

    overview = queries.monthly(client, 2026, 9)["reply"]  # type: ignore[arg-type]
    assert overview.startswith("📊 2026-09: spent Rp1,2jt of Rp1jt planned")
    assert "⚠️ Over budget: Food" in overview

    unknown = queries.monthly(client, 2026, 9, "pets")  # type: ignore[arg-type]
    assert unknown["status"] == "needs_input"
    assert unknown["options"]["category"] == ["Food", "Shopping"]


def test_find_resolves_names_and_labels_the_sum_as_a_list_total() -> None:
    names = Names(
        categories=[{"id": "c-food", "name": "Food", "kind": "need"}],
        accounts=[{"id": "a-bca", "name": "BCA"}],
    )
    client = FakeReports()
    result = queries.find(client, names, {"q": "kopi"}, category="food", limit=5)  # type: ignore[arg-type]

    assert client.filters == {"q": "kopi", "category_id": "c-food"}
    assert result["listed_expense_total"] == 30_000
    assert result["reply"].startswith("🔎 2 transaction(s); expenses in this list sum to Rp30.000")
    assert [item["id"] for item in result["items"]] == ["t1", "t2"]
