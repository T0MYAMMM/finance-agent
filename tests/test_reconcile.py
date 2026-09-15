import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from doeedd_agent.parsing import ParseError
from doeedd_agent.reconcile import (
    StatementLine,
    load_statement,
    movement,
    reconcile,
    statement_window,
)

TODAY = date(2026, 10, 2)
BCA = "a-bca"


def transaction(id_: str, day: str, kind: str, amount: int, **extra: Any) -> dict[str, Any]:
    return {
        "id": id_,
        "occurred_on": day,
        "type": kind,
        "amount": amount,
        "account_id": extra.pop("account_id", BCA),
        "transfer_to_account_id": extra.pop("to", None),
        "deleted_at": extra.pop("deleted_at", None),
        **extra,
    }


def test_loads_statement_lines_in_the_owners_words(tmp_path: Path) -> None:
    file = tmp_path / "statement.json"
    file.write_text(
        json.dumps(
            [
                {
                    "date": "05/09/2026",
                    "amount": "Rp150.000",
                    "direction": "DB",
                    "description": "INDOMARET",
                },
                {
                    "date": "2026-09-25",
                    "amount": 15000000,
                    "direction": "masuk",
                    "description": "GAJI",
                },
            ]
        )
    )
    lines = load_statement(file, TODAY)
    assert lines == [
        StatementLine(1, date(2026, 9, 5), 150_000, "debit", "INDOMARET"),
        StatementLine(2, date(2026, 9, 25), 15_000_000, "credit", "GAJI"),
    ]


@pytest.mark.parametrize(
    "content",
    ["[]", "{}", '[{"date": "2026-09-05", "amount": 1000, "direction": "sideways"}]', "not json"],
)
def test_rejects_unusable_statement_files(tmp_path: Path, content: str) -> None:
    file = tmp_path / "statement.json"
    file.write_text(content)
    with pytest.raises(ParseError):
        load_statement(file, TODAY)


def test_movement_direction_per_account() -> None:
    assert movement(transaction("t1", "2026-09-01", "expense", 1), BCA) == "debit"
    assert movement(transaction("t2", "2026-09-01", "income", 1), BCA) == "credit"
    assert movement(transaction("t3", "2026-09-01", "transfer", 1, to="a-gopay"), BCA) == "debit"
    incoming = transaction("t4", "2026-09-01", "transfer", 1, account_id="a-mandiri", to=BCA)
    assert movement(incoming, BCA) == "credit"
    assert (
        movement(transaction("t5", "2026-09-01", "expense", 1, account_id="a-gopay"), BCA) is None
    )


def test_pairs_lines_within_a_day_and_lists_both_sides() -> None:
    lines = [
        StatementLine(1, date(2026, 9, 5), 150_000, "debit", "INDOMARET"),
        StatementLine(2, date(2026, 9, 6), 27_600, "debit", "QRIS TOMORO"),
        StatementLine(3, date(2026, 9, 6), 27_600, "debit", "QRIS TOMORO"),
        StatementLine(4, date(2026, 9, 25), 15_000_000, "credit", "GAJI"),
    ]
    transactions = [
        transaction("t-tomoro-1", "2026-09-06", "expense", 27_600),
        transaction("t-tomoro-2", "2026-09-07", "expense", 27_600),
        transaction("t-salary", "2026-09-25", "income", 15_000_000),
        transaction("t-lunch", "2026-09-10", "expense", 45_000),
        transaction(
            "t-deleted", "2026-09-05", "expense", 150_000, deleted_at="2026-09-06T00:00:00Z"
        ),
    ]
    result = reconcile(lines, transactions, BCA)

    assert [(line.number, item["id"]) for line, item in result.matched] == [
        (2, "t-tomoro-1"),
        (3, "t-tomoro-2"),
        (4, "t-salary"),
    ]
    assert [line.number for line in result.missing] == [1]
    assert [item["id"] for item in result.extra] == ["t-lunch"]
    assert statement_window(lines) == (date(2026, 9, 4), date(2026, 9, 26))


def test_does_not_match_across_direction_or_beyond_tolerance() -> None:
    lines = [StatementLine(1, date(2026, 9, 5), 100_000, "credit")]
    transactions = [
        transaction("t-out", "2026-09-05", "expense", 100_000),
        transaction("t-late", "2026-09-08", "income", 100_000),
    ]
    result = reconcile(lines, transactions, BCA)
    assert result.matched == []
    assert [line.number for line in result.missing] == [1]
    assert {item["id"] for item in result.extra} == {"t-out", "t-late"}
