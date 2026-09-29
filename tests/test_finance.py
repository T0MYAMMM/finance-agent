from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from finance_agent.config import Settings
from finance_agent.domain import InputError
from finance_agent.service import FinanceService
from finance_agent.storage import Ledger


@pytest.fixture
def service(tmp_path: Path):
    ledger = Ledger(tmp_path / "finance.sqlite3")
    app = FinanceService(
        Settings(tmp_path, "IDR", ZoneInfo("Asia/Jakarta")), ledger, date(2026, 9, 29)
    )
    yield app
    ledger.close()


def test_record_is_idempotent_and_flags_duplicate(service: FinanceService) -> None:
    first = service.record(
        amount_text="45000",
        date_text="2026-09-10",
        merchant="Cafe",
        category="Food & Dining",
        external_ref="telegram:1:2:1",
    )
    replay = service.record(
        amount_text="45000", date_text="2026-09-10", merchant="Cafe", external_ref="telegram:1:2:1"
    )
    second = service.record(
        amount_text="45000", date_text="2026-09-10", merchant="Cafe", category="Food & Dining"
    )
    assert first["status"] == "created"
    assert replay["status"] == "replayed"
    assert second["possible_duplicate"] is True
    assert len(service.find()["items"]) == 2
    assert len(service.duplicates()["items"]) == 2


def test_equivalent_decimal_text_is_duplicate(service: FinanceService) -> None:
    service.record(amount_text="45.0", merchant="Cafe")
    second = service.record(amount_text="45", merchant="Cafe")
    assert second["possible_duplicate"] is True


def test_summary_keeps_currency_separate_and_refund_reduces_expense(
    service: FinanceService,
) -> None:
    service.record(amount_text="100", type_text="income", currency="IDR")
    service.record(amount_text="40", type_text="expense", currency="IDR")
    service.record(amount_text="10", type_text="refund", currency="IDR")
    service.record(amount_text="5", type_text="expense", currency="USD")
    service.record(amount_text="25", type_text="transfer", account="A", to_account="B")
    totals = service.summary()["totals"]
    assert totals["IDR"] == {"income": "100", "expense": "30", "transfers": "25", "net": "70"}
    assert totals["USD"]["net"] == "-5"


def test_receipt_and_month_reconciliation(service: FinanceService, tmp_path: Path) -> None:
    receipt = tmp_path / "receipt.jpg"
    receipt.write_bytes(b"image")
    result = service.record(amount_text="200", date_text="2026-09-01", receipt=str(receipt))
    assert Path(result["transaction"]["receipt_path"]).is_file()
    assert service.find(missing_receipt=True)["items"] == []
    assert service.reconcile("2026-09")["count"] == 1
    assert service.find()["items"][0]["reconciliation_status"] == "Reconciled"


def test_invalid_transfer_and_unknown_category_write_nothing(service: FinanceService) -> None:
    with pytest.raises(InputError):
        service.record(amount_text="10", type_text="transfer", account="A", to_account="A")
    with pytest.raises(InputError):
        service.record(amount_text="10", category="Unknown")
    assert service.find()["items"] == []
