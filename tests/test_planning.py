import copy
from typing import Any

import pytest

from doeedd_agent.aliases import DEFAULT_ALIASES
from doeedd_agent.capture import Names
from doeedd_agent.cli import _line_change
from doeedd_agent.parsing import ParseError
from doeedd_agent.planning import move_budget, set_budget, show_plan

CATEGORIES = [
    {"id": "c-salary", "name": "Monthly Salary", "kind": "income"},
    {"id": "c-save", "name": "General Savings", "kind": "saving"},
    {"id": "c-food", "name": "Food", "kind": "need"},
    {"id": "c-shop", "name": "Shopping", "kind": "want"},
    {"id": "c-fun", "name": "Entertainment", "kind": "want"},
]
NAMES = Names(categories=CATEGORIES, accounts=[{"id": "a-mandiri", "name": "Mandiri"}])
ALIASES = DEFAULT_ALIASES["category"]


def line(category_id: str, amount: int, funding: str | None = None) -> dict[str, Any]:
    category = next(item for item in CATEGORIES if item["id"] == category_id)
    return {
        "id": f"l-{category_id}",
        "category_id": category_id,
        "category": {**category, "archived_at": None},
        "planned_amount": amount,
        "funding_account_id": funding,
    }


class FakeBudgets:
    def __init__(self) -> None:
        self.periods: dict[tuple[int, int], dict[str, Any]] = {}
        self.puts: list[list[dict[str, Any]]] = []

    def budget_period(self, year: int, month: int) -> dict[str, Any] | None:
        period = self.periods.get((year, month))
        return copy.deepcopy(period) if period else None

    def put_budget_lines(self, year: int, month: int, lines: list[dict[str, Any]]):
        self.puts.append(lines)
        period = self.periods.setdefault((year, month), {"lines": []})
        by_category = {item["category_id"]: item for item in period["lines"]}
        for change in lines:
            updated = line(
                change["category_id"], change["planned_amount"], change["funding_account_id"]
            )
            by_category[change["category_id"]] = updated
        period["lines"] = list(by_category.values())
        period["totals"] = totals(period["lines"])
        return copy.deepcopy(period)


def totals(lines: list[dict[str, Any]]) -> dict[str, Any]:
    def total(*kinds: str) -> int:
        return sum(item["planned_amount"] for item in lines if item["category"]["kind"] in kinds)

    income, spending = total("income"), total("saving", "need", "want")
    return {
        "planned_income": income,
        "allocated_pct": spending / income if income else None,
        "non_allocated": income - spending,
    }


@pytest.fixture
def budgets() -> FakeBudgets:
    fake = FakeBudgets()
    lines = [
        line("c-salary", 10_000_000),
        line("c-save", 2_000_000, "a-mandiri"),
        line("c-food", 1_500_000, "a-mandiri"),
        line("c-shop", 1_000_000),
    ]
    fake.periods[(2026, 9)] = {"lines": lines, "totals": totals(lines)}
    return fake


def test_show_plan_groups_by_kind(budgets: FakeBudgets) -> None:
    reply = show_plan(budgets, 2026, 9)["reply"]  # type: ignore[arg-type]
    assert reply.splitlines() == [
        "🗂️ Plan 2026-09:",
        "Income: Monthly Salary Rp10jt",
        "Savings: General Savings Rp2jt",
        "Needs: Food Rp1,5jt",
        "Wants: Shopping Rp1jt",
        "Allocated 45% of income · unallocated Rp5,5jt",
    ]
    assert show_plan(budgets, 2026, 10)["reply"] == "No budget planned for 2026-10 yet."  # type: ignore[arg-type]


def test_set_budget_uses_aliases_and_keeps_funding_accounts(budgets: FakeBudgets) -> None:
    result = set_budget(budgets, NAMES, ALIASES, 2026, 9, [("makan", 1_800_000)])  # type: ignore[arg-type]
    assert budgets.puts[-1] == [
        {"category_id": "c-food", "planned_amount": 1_800_000, "funding_account_id": "a-mandiri"}
    ]
    assert result["reply"] == (
        "🗂️ 2026-09 plan updated: Food Rp1,8jt. Allocated 48% of income · unallocated Rp5,2jt."
    )


def test_set_budget_asks_for_unknown_categories_and_warns_on_over_allocation(
    budgets: FakeBudgets,
) -> None:
    unknown = set_budget(budgets, NAMES, ALIASES, 2026, 9, [("pets", 100_000)])  # type: ignore[arg-type]
    assert unknown["status"] == "needs_input"
    assert budgets.puts == []

    over = set_budget(budgets, NAMES, ALIASES, 2026, 9, [("Shopping", 9_000_000)])  # type: ignore[arg-type]
    assert over["reply"].endswith("⚠️ More is planned than the planned income.")


def test_move_budget_between_categories(budgets: FakeBudgets) -> None:
    result = move_budget(budgets, NAMES, ALIASES, 2026, 9, "shopping", "hiburan", 200_000)  # type: ignore[arg-type]
    assert budgets.puts[-1] == [
        {"category_id": "c-shop", "planned_amount": 800_000, "funding_account_id": None},
        {"category_id": "c-fun", "planned_amount": 200_000, "funding_account_id": None},
    ]
    assert result["reply"] == (
        "🔀 Moved Rp200.000 from Shopping to Entertainment for 2026-09 "
        "(Shopping Rp800.000, Entertainment Rp200.000)."
    )

    too_much = move_budget(budgets, NAMES, ALIASES, 2026, 9, "Shopping", "Food", 5_000_000)  # type: ignore[arg-type]
    assert too_much["status"] == "needs_input"
    assert too_much["reply"] == "Shopping only has Rp800.000 planned for 2026-09."


def test_line_change_parsing() -> None:
    assert _line_change("Food=1,5jt") == ("Food", 1_500_000)
    assert _line_change("Food & Dining = 250rb") == ("Food & Dining", 250_000)
    with pytest.raises(ParseError):
        _line_change("Food 1,5jt")
