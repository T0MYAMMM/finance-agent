from pathlib import Path
from typing import Any

from doeedd_agent import queries
from doeedd_agent.aliases import AliasStore
from doeedd_agent.capture import Names, Recorder
from doeedd_agent.outbox import Outbox
from doeedd_agent.state import StateStore

NAMES = Names(
    categories=[{"id": "c-food", "name": "Food", "kind": "need"}],
    accounts=[{"id": "a-bca", "name": "BCA"}, {"id": "a-gopay", "name": "GoPay"}],
)
BASE = {"deleted_at": None, "is_reviewed": False, "source": "agent", "payment_method": None}
ROWS = [
    {
        **BASE,
        "id": "t1",
        "occurred_on": "2026-09-14",
        "type": "expense",
        "amount": 10_000,
        "category_id": "c-food",
        "account_id": "a-bca",
        "transfer_to_account_id": None,
        "merchant": "Tomoro",
        "description": "Latte",
    },
    {
        **BASE,
        "id": "t2",
        "occurred_on": "2026-09-13",
        "type": "transfer",
        "amount": 500_000,
        "category_id": None,
        "account_id": "a-bca",
        "transfer_to_account_id": "a-gopay",
        "merchant": None,
        "description": "Top-up",
    },
]


class FakeDoeedd:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.reviewed: list[tuple[list[str], bool]] = []

    def transactions(self, filters: dict[str, Any], *, max_items: int | None = None):
        assert filters == {"reviewed": False}
        return self.rows

    def attachments(self, transaction_id: str) -> list[dict[str, Any]]:
        if transaction_id != "t1":
            return []
        return [{"filename": "r.jpg", "url": "https://drive.google.com/file/d/x/view"}]

    def bulk_review(self, ids: list[str], is_reviewed: bool) -> dict[str, int]:
        self.reviewed.append((ids, is_reviewed))
        return {"updated": len(ids)}


def test_review_lists_numbered_entries() -> None:
    result = queries.review(FakeDoeedd(ROWS), NAMES)  # type: ignore[arg-type]
    assert result["reply"].splitlines() == [
        "🧾 2 entries to review:",
        "1. 2026-09-14 Rp10.000 Food Tomoro",
        "2. 2026-09-13 Rp500.000 BCA → GoPay Top-up",
        'Reply "approve all", or tell me which numbers to fix.',
    ]
    assert queries.review(FakeDoeedd([]), NAMES)["reply"] == "✅ Nothing to review."  # type: ignore[arg-type]


def test_receipts_show_links_or_say_there_are_none() -> None:
    doeedd = FakeDoeedd(ROWS)
    linked = queries.receipts(doeedd, NAMES, ROWS[0])  # type: ignore[arg-type]
    assert linked["reply"] == (
        "📎 2026-09-14 Rp10.000 Food Tomoro\n• r.jpg: https://drive.google.com/file/d/x/view"
    )
    none = queries.receipts(doeedd, NAMES, ROWS[1])  # type: ignore[arg-type]
    assert none["reply"] == "No receipt linked to 2026-09-13 Rp500.000 BCA → GoPay Top-up."


def test_approve_marks_entries_reviewed(tmp_path: Path) -> None:
    doeedd = FakeDoeedd(ROWS)
    recorder = Recorder(
        doeedd,  # type: ignore[arg-type]
        AliasStore(tmp_path / "aliases.json"),
        StateStore(tmp_path / "state.json"),
        Outbox(tmp_path / "outbox.json"),
    )
    outcome = recorder.approve(["t1", "t2"])
    assert outcome.status == "reviewed"
    assert outcome.reply == "👍 Marked 2 entries as reviewed."
    assert doeedd.reviewed == [(["t1", "t2"], True)]
    assert recorder.approve([]).status == "nothing"
