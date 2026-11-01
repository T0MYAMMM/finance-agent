from datetime import date
from typing import Any

from doeedd_agent.holdings import account_balances


class FakeBalances:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items
        self.asked: list[date] = []

    def account_balances(self, at: date, *, include_archived: bool = False) -> dict[str, Any]:
        self.asked.append(at)
        return {"at": at.isoformat(), "items": self.items}


def test_balances_list_each_account() -> None:
    client = FakeBalances(
        [
            {"account": {"name": "BCA"}, "balance": 5_550_000},
            {"account": {"name": "Credit Card"}, "balance": -1_250_000},
        ]
    )
    result = account_balances(client, date(2026, 9, 15))  # type: ignore[arg-type]
    assert result["reply"].splitlines() == [
        "💳 Balances on 15 Sep (from logged entries):",
        "• BCA: Rp5,6jt",
        "• Credit Card: -Rp1,3jt",
        "If one looks off, its opening balance or a missing entry is the usual cause.",
    ]
    assert client.asked == [date(2026, 9, 15)]


def test_balances_without_accounts() -> None:
    result = account_balances(FakeBalances([]), date(2026, 9, 15))  # type: ignore[arg-type]
    assert result["reply"] == "No accounts yet."
