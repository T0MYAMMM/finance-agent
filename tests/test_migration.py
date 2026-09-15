from typing import Any

from doeedd_agent.aliases import DEFAULT_ALIASES
from doeedd_agent.capture import Names
from doeedd_agent.client import DoeeddError
from doeedd_agent.migration import (
    PlannedRow,
    apply_migration,
    expense_total,
    plan_migration,
    read_ledger,
)

NAMES = Names(
    categories=[
        {"id": "c-food", "name": "Food", "kind": "need"},
        {"id": "c-salary", "name": "Monthly Salary", "kind": "income"},
    ],
    accounts=[
        {"id": "a-bca", "name": "BCA"},
        {"id": "a-mandiri", "name": "Mandiri"},
        {"id": "a-gopay", "name": "GoPay"},
    ],
)


def ledger_row(sheet_row: int, **overrides: Any) -> dict[str, Any]:
    row = {
        "transaction_id": "TXN-20260901-001",
        "transaction_date": "2026-09-01",
        "transaction_type": "Expense",
        "description": "Latte",
        "merchant": "Tomoro Coffee",
        "category": "Food & Dining",
        "subcategory": "Coffee",
        "amount": 27600,
        "currency": "IDR",
        "payment_method": "QRIS",
        "account": "Mandiri",
        "receipt_link": "https://drive.google.com/file/d/drive-1/view",
        "receipt_file_id": "drive-1",
        "receipt_filename": "2026-09_coffee-orders_tomoro-coffee_01-03.jpg",
        "duplicate_status": "",
        "reconciliation_status": "Pending",
        "source": "Manual",
        "notes": "Wisma 46 (office)",
        "_sheet_row": sheet_row,
    }
    row.update(overrides)
    return row


def test_maps_a_ledger_row_keeping_every_attribute() -> None:
    planned, skipped = plan_migration([ledger_row(2)], NAMES, DEFAULT_ALIASES)
    assert skipped == []
    row = planned[0]
    assert row.body == {
        "occurred_on": "2026-09-01",
        "description": "Latte",
        "type": "expense",
        "category_id": "c-food",
        "account_id": "a-mandiri",
        "transfer_to_account_id": None,
        "amount": 27600,
        "is_reviewed": False,
        "notes": "Wisma 46 (office); [migrated from Sheets TXN-20260901-001]",
        "merchant": "Tomoro Coffee",
        "subcategory": "Coffee",
        "payment_method": "QRIS",
        "source": "import",
        "external_ref": "sheets:TXN-20260901-001",
        "possible_duplicate": False,
    }
    assert row.attachment == {
        "provider": "gdrive",
        "external_id": "drive-1",
        "url": "https://drive.google.com/file/d/drive-1/view",
        "filename": "2026-09_coffee-orders_tomoro-coffee_01-03.jpg",
        "mime_type": "image/jpeg",
    }


def test_skips_rows_that_would_need_a_guess() -> None:
    rows = [
        ledger_row(10, transaction_id="TXN-20230422-009", account="", payment_method=""),
        ledger_row(11, transaction_id="TXN-R", transaction_type="Refund"),
        ledger_row(12, transaction_id="TXN-F", amount=45.5),
        ledger_row(13, transaction_id="TXN-C", category="Pets"),
        ledger_row(14, transaction_id="TXN-T", transaction_type="Transfer", notes="top-up"),
    ]
    planned, skipped = plan_migration(rows, NAMES, DEFAULT_ALIASES)
    assert planned == []
    reasons = {row.ref: " ".join(row.reasons) for row in skipped}
    assert "no account" in reasons["TXN-20230422-009"]
    assert "not imported" in reasons["TXN-R"]
    assert "not a whole rupiah" in reasons["TXN-F"]
    assert "'Pets' has no doeedd match" in reasons["TXN-C"]
    assert "transfer destination" in reasons["TXN-T"]
    assert skipped[0].sheet_row == 10


def test_transfers_reviews_duplicates_and_small_amounts() -> None:
    rows = [
        ledger_row(
            2,
            transaction_id="TXN-T",
            transaction_type="Transfer",
            category="Other",
            account="BCA",
            notes="BCA -> GoPay",
            amount=500000,
            receipt_file_id="",
            receipt_link="",
        ),
        ledger_row(
            3,
            transaction_id="TXN-S",
            amount=900,
            reconciliation_status="Reconciled",
            duplicate_status="POSSIBLE_DUPLICATE",
            source="Receipt",
        ),
    ]
    planned, skipped = plan_migration(rows, NAMES, DEFAULT_ALIASES)
    assert skipped == []
    transfer, small = planned
    assert transfer.body["transfer_to_account_id"] == "a-gopay"
    assert transfer.body["category_id"] is None
    assert transfer.attachment is None
    assert small.body["is_reviewed"] is True
    assert small.body["possible_duplicate"] is True
    assert small.body["notes"].endswith("[source: Receipt]")
    assert small.warnings and "under Rp1.000" in small.warnings[0]


class FakeSheets:
    def __init__(self, values: list[list[Any]]) -> None:
        self.values_data = values

    def spreadsheets(self) -> "FakeSheets":
        return self

    def values(self) -> "FakeSheets":
        return self

    def get(self, **_: Any) -> "FakeSheets":
        return self

    def execute(self) -> dict[str, Any]:
        return {"values": self.values_data}


def test_reads_only_real_rows_and_totals_expenses() -> None:
    sheets = FakeSheets(
        [
            ["transaction_id", "transaction_date", "transaction_type", "amount"],
            ["TXN-1", "2026-09-01", "Expense", 27600],
            ["", "", "", "", True],  # a pre-filled formula row
            ["TXN-2", "2026-09-02", "Income", 1000000],
            ["TXN-3", "2026-09-03", "Expense", 24100],
        ]
    )
    rows = read_ledger(sheets, "sheet-id")
    assert [row["transaction_id"] for row in rows] == ["TXN-1", "TXN-2", "TXN-3"]
    assert rows[2]["_sheet_row"] == 5
    assert expense_total(rows) == 51_700


class FakeDoeedd:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.links: list[tuple[str, str]] = []

    def transactions(self, filters: dict[str, Any], *, max_items: int | None = None):
        return [row for row in self.rows if row["external_ref"] == filters["external_ref"]]

    def create_transaction(self, body: dict[str, Any], idempotency_key: str | None = None):
        row = {**body, "id": f"tx-{len(self.rows) + 1}"}
        self.rows.append(row)
        return row, False

    def add_attachment(self, transaction_id: str, body: dict[str, Any]) -> dict[str, Any]:
        link = (transaction_id, body["external_id"])
        if link in self.links:
            raise DoeeddError(409, "Conflict")
        self.links.append(link)
        return body


def test_apply_is_idempotent() -> None:
    planned, _ = plan_migration([ledger_row(2)], NAMES, DEFAULT_ALIASES)
    doeedd = FakeDoeedd()

    first = apply_migration(doeedd, planned)  # type: ignore[arg-type]
    again = apply_migration(doeedd, planned)  # type: ignore[arg-type]

    assert first == {"created": ["TXN-20260901-001"], "already_present": [], "receipts_linked": 1}
    assert again == {"created": [], "already_present": ["TXN-20260901-001"], "receipts_linked": 0}
    assert len(doeedd.rows) == 1
    assert isinstance(planned[0], PlannedRow)
