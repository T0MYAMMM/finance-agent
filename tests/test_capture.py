from datetime import date
from pathlib import Path
from typing import Any

import pytest

from doeedd_agent.aliases import AliasStore
from doeedd_agent.capture import CaptureRequest, EditRequest, Recorder
from doeedd_agent.client import DoeeddError
from doeedd_agent.outbox import Outbox
from doeedd_agent.receipts import ReceiptUploadError
from doeedd_agent.state import StateStore

TODAY = date(2026, 9, 15)
CATEGORIES = [
    {"id": "c-salary", "name": "Monthly Salary", "kind": "income"},
    {"id": "c-food", "name": "Food", "kind": "need"},
    {"id": "c-shop", "name": "Shopping", "kind": "want"},
    {"id": "c-save", "name": "General Savings", "kind": "saving"},
]
ACCOUNTS = [
    {"id": "a-bca", "name": "BCA"},
    {"id": "a-mandiri", "name": "Mandiri"},
    {"id": "a-gopay", "name": "GoPay"},
]


class FakeDoeedd:
    """Just enough of DoeeddClient for the recorder, backed by lists."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.created: list[tuple[dict[str, Any], str | None]] = []
        self.attachments: list[tuple[str, dict[str, Any]]] = []
        self.create_error: DoeeddError | None = None
        self.events: list[str] = []

    def categories(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        return CATEGORIES

    def accounts(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        return ACCOUNTS

    def transactions(
        self, filters: dict[str, Any], *, max_items: int | None = None
    ) -> list[dict[str, Any]]:
        live = [row for row in self.rows if row["deleted_at"] is None]
        if "external_ref" in filters:
            return [row for row in live if row["external_ref"] == filters["external_ref"]]
        start, end = filters["from"].isoformat(), filters["to"].isoformat()
        return [
            row
            for row in live
            if start <= row["occurred_on"] <= end and row["type"] == filters["type"]
        ]

    def create_transaction(self, body: dict[str, Any], idempotency_key: str | None = None):
        if self.create_error is not None:
            raise self.create_error
        self.events.append("create")
        row = {**body, "id": f"tx-{len(self.rows) + 1}", "deleted_at": None}
        self.rows.append(row)
        self.created.append((body, idempotency_key))
        return row, False

    def add_attachment(self, transaction_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.attachments.append((transaction_id, body))
        return {"id": "att-1", **body}

    def report_monthly(self, year: int, month: int) -> dict[str, Any]:
        return {
            "categories": [
                {
                    "category": {"id": "c-food", "name": "Food"},
                    "planned": 1_000_000,
                    "actual": 600_000,
                    "usage": 0.6,
                    "status": "ok",
                }
            ]
        }

    def _row(self, transaction_id: str) -> dict[str, Any]:
        return next(row for row in self.rows if row["id"] == transaction_id)

    def get_transaction(self, transaction_id: str) -> dict[str, Any]:
        return self._row(transaction_id)

    def update_transaction(self, transaction_id: str, body: dict[str, Any]) -> dict[str, Any]:
        row = self._row(transaction_id)
        row.update(body)
        return row

    def delete_transaction(self, transaction_id: str) -> None:
        self._row(transaction_id)["deleted_at"] = "2026-09-15T00:00:00Z"

    def restore_transaction(self, transaction_id: str) -> dict[str, Any]:
        row = self._row(transaction_id)
        row["deleted_at"] = None
        return row


@pytest.fixture
def doeedd() -> FakeDoeedd:
    return FakeDoeedd()


@pytest.fixture
def recorder(doeedd: FakeDoeedd, tmp_path: Path) -> Recorder:
    return Recorder(
        doeedd,  # type: ignore[arg-type]
        AliasStore(tmp_path / "aliases.json"),
        StateStore(tmp_path / "state.json"),
        Outbox(tmp_path / "outbox.json"),
    )


def expense(**overrides: Any) -> CaptureRequest:
    fields: dict[str, Any] = {"amount": 27_600, "occurred_on": date(2026, 9, 14)}
    fields.update(overrides)
    return CaptureRequest(**fields)


def test_records_an_expense_using_merchant_defaults(recorder: Recorder, doeedd: FakeDoeedd) -> None:
    outcome = recorder.capture(expense(merchant="Tomoro Coffee", key="telegram:1:10:1"), TODAY)

    assert outcome.status == "created"
    body, key = doeedd.created[0]
    assert key == "telegram:1:10:1"
    assert body["category_id"] == "c-food"
    assert body["account_id"] == "a-mandiri"
    assert body["payment_method"] == "QRIS"
    assert body["subcategory"] == "Coffee"
    assert body["source"] == "agent"
    assert body["external_ref"] == "telegram:1:10:1"
    assert outcome.usage == {
        "category": "Food",
        "planned": 1_000_000,
        "actual": 600_000,
        "usage": 0.6,
        "status": "ok",
    }
    assert "Rp27.600 · Food · Mandiri · 14 Sep — Tomoro Coffee" in outcome.reply
    assert "Food 60% of budget" in outcome.reply


def test_asks_instead_of_guessing_an_unknown_category(
    recorder: Recorder, doeedd: FakeDoeedd
) -> None:
    outcome = recorder.capture(expense(merchant="Starbucks"), TODAY)
    assert outcome.status == "needs_input"
    assert outcome.missing == ["category"]
    assert outcome.options["category"] == ["Food", "Shopping", "General Savings"]
    assert doeedd.created == []


def test_ambiguous_words_are_asked_about(recorder: Recorder) -> None:
    outcome = recorder.capture(expense(category="belanja"), TODAY)
    assert outcome.missing == ["category"]


def test_account_comes_from_payment_method_then_habit(
    recorder: Recorder, doeedd: FakeDoeedd
) -> None:
    recorder.capture(expense(category="kopi", payment_method="gopay"), TODAY)
    assert doeedd.created[-1][0]["account_id"] == "a-gopay"

    recorder.capture(expense(category="Food", amount=15_000), TODAY)
    assert doeedd.created[-1][0]["account_id"] == "a-gopay"

    recorder.capture(expense(category="Shopping", amount=99_000), TODAY)
    assert doeedd.created[-1][0]["account_id"] == "a-bca"  # default account


def test_transfer_needs_both_accounts_and_no_category(
    recorder: Recorder, doeedd: FakeDoeedd
) -> None:
    missing = recorder.capture(expense(type="transfer", account="BCA", amount=500_000), TODAY)
    assert missing.missing == ["to_account"]

    outcome = recorder.capture(
        expense(type="transfer", account="BCA", to_account="gopay", amount=500_000), TODAY
    )
    body = doeedd.created[-1][0]
    assert body["category_id"] is None
    assert body["transfer_to_account_id"] == "a-gopay"
    assert "not counted as spending" in outcome.reply


def test_income_needs_an_income_category(recorder: Recorder, doeedd: FakeDoeedd) -> None:
    outcome = recorder.capture(
        expense(type="income", category="gajian", account="BCA", amount=15_000_000), TODAY
    )
    assert outcome.status == "created"
    assert doeedd.created[-1][0]["category_id"] == "c-salary"


def test_flags_a_likely_duplicate_until_confirmed(recorder: Recorder, doeedd: FakeDoeedd) -> None:
    recorder.capture(expense(merchant="Tomoro Coffee"), TODAY)
    again = recorder.capture(expense(merchant="tomoro coffee"), TODAY)
    assert again.status == "possible_duplicate"
    assert again.duplicates[0]["merchant"] == "Tomoro Coffee"
    assert len(doeedd.created) == 1

    confirmed = recorder.capture(expense(merchant="Tomoro Coffee", allow_duplicate=True), TODAY)
    assert confirmed.status == "created"
    assert doeedd.created[-1][0]["possible_duplicate"] is True


def test_same_message_key_replays_instead_of_duplicating(
    recorder: Recorder, doeedd: FakeDoeedd
) -> None:
    recorder.capture(expense(merchant="Tomoro Coffee", key="telegram:1:10:1"), TODAY)
    again = recorder.capture(expense(merchant="Tomoro Coffee", key="telegram:1:10:1"), TODAY)
    assert again.status == "replayed"
    assert len(doeedd.created) == 1


def test_future_dates_need_confirmation(recorder: Recorder) -> None:
    outcome = recorder.capture(
        expense(merchant="Tomoro Coffee", occurred_on=date(2026, 9, 20)), TODAY
    )
    assert outcome.missing == ["date"]


def test_queues_when_doeedd_is_unreachable_and_flushes_later(
    recorder: Recorder, doeedd: FakeDoeedd
) -> None:
    doeedd.create_error = DoeeddError(None, "doeedd is unreachable")
    outcome = recorder.capture(expense(merchant="Tomoro Coffee", key="telegram:1:11:1"), TODAY)
    assert outcome.status == "queued"
    assert len(recorder.outbox) == 1

    doeedd.create_error = None
    flushed = recorder.flush()
    assert flushed.status == "flushed"
    assert len(recorder.outbox) == 0
    assert doeedd.created[0][1] == "telegram:1:11:1"


def test_uploads_receipt_before_recording_and_links_it(doeedd: FakeDoeedd, tmp_path: Path) -> None:
    def uploader(
        path: Path, occurred_on: date, merchant: str | None, amount: int, category: str | None
    ):
        doeedd.events.append("upload")
        return {
            "file_id": "drive-1",
            "name": "r.jpg",
            "url": "https://drive.google.com/file/d/drive-1/view",
            "mime_type": "image/jpeg",
        }

    recorder = Recorder(
        doeedd,  # type: ignore[arg-type]
        AliasStore(tmp_path / "aliases.json"),
        StateStore(tmp_path / "state.json"),
        Outbox(tmp_path / "outbox.json"),
        uploader=uploader,
    )
    outcome = recorder.capture(
        expense(merchant="Tomoro Coffee", receipt_path=tmp_path / "r.jpg"), TODAY
    )
    assert doeedd.events == ["upload", "create"]
    transaction_id, attachment = doeedd.attachments[0]
    assert transaction_id == outcome.transaction["id"]
    assert attachment["provider"] == "gdrive"
    assert attachment["external_id"] == "drive-1"


def test_failed_upload_records_nothing(doeedd: FakeDoeedd, tmp_path: Path) -> None:
    def uploader(*_: Any) -> dict[str, Any]:
        raise ReceiptUploadError("Drive upload failed")

    recorder = Recorder(
        doeedd,  # type: ignore[arg-type]
        AliasStore(tmp_path / "aliases.json"),
        StateStore(tmp_path / "state.json"),
        Outbox(tmp_path / "outbox.json"),
        uploader=uploader,
    )
    with pytest.raises(ReceiptUploadError):
        recorder.capture(expense(merchant="Tomoro Coffee", receipt_path=tmp_path / "r.jpg"), TODAY)
    assert doeedd.created == []


def test_undo_and_restore_cover_every_entry_of_a_message(
    recorder: Recorder, doeedd: FakeDoeedd
) -> None:
    recorder.capture(expense(category="Shopping", amount=5_000, key="telegram:1:12:1"), TODAY)
    recorder.capture(expense(category="Food", amount=30_000, key="telegram:1:12:2"), TODAY)

    undone = recorder.undo()
    assert undone.status == "deleted"
    assert all(row["deleted_at"] for row in doeedd.rows)

    restored = recorder.restore()
    assert restored.status == "restored"
    assert len(restored.transactions) == 2
    assert not any(row["deleted_at"] for row in doeedd.rows)


def test_edit_corrects_the_last_entry_and_learns_the_merchant(
    recorder: Recorder, doeedd: FakeDoeedd
) -> None:
    recorder.capture(expense(merchant="Kopi Kenangan", category="Food", account="BCA"), TODAY)
    outcome = recorder.edit(None, EditRequest(category="Shopping", amount=35_000, learn=True))

    assert outcome.status == "updated"
    assert doeedd.rows[0]["category_id"] == "c-shop"
    assert doeedd.rows[0]["amount"] == 35_000
    assert recorder.aliases.merged()["merchant_defaults"]["kopi kenangan"]["category"] == "Shopping"
