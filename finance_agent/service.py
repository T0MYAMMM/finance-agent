"""Finance use cases shared by CLI and future agent transports."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .config import Settings
from .domain import InputError, amount, iso_date, month, normalize_type
from .receipts import archive
from .storage import Ledger


class FinanceService:
    def __init__(self, settings: Settings, ledger: Ledger, today: date | None = None) -> None:
        self.settings = settings
        self.ledger = ledger
        self.today = today or datetime.now(settings.timezone).date()

    def upload(
        self,
        source: str,
        *,
        date_text: str | None = None,
        merchant: str = "",
        amount_text: str = "",
    ) -> dict[str, Any]:
        day = iso_date(date_text, self.today)
        if amount_text:
            amount(amount_text)
        identifier, path = archive(Path(source), self.settings.data_dir, day, merchant, amount_text)
        self.ledger.save_receipt(identifier, path)
        return {
            "status": "uploaded",
            "receipt_id": identifier,
            "path": path,
            "reply": f"Receipt stored: {identifier}",
        }

    def _receipt(
        self, value: str | None, day: str, merchant: str, amount_text: str
    ) -> tuple[str, str]:
        if not value:
            return "", ""
        stored = self.ledger.receipt(value)
        if stored:
            return value, stored
        if Path(value).expanduser().is_file():
            uploaded = self.upload(value, date_text=day, merchant=merchant, amount_text=amount_text)
            return uploaded["receipt_id"], uploaded["path"]
        raise InputError("Receipt must be an existing receipt ID or local file path")

    def record(
        self,
        *,
        amount_text: str,
        type_text: str = "Expense",
        date_text: str | None = None,
        merchant: str = "",
        description: str = "",
        category: str = "",
        account: str = "",
        to_account: str = "",
        payment_method: str = "",
        notes: str = "",
        receipt: str | None = None,
        external_ref: str | None = None,
        currency: str | None = None,
    ) -> dict[str, Any]:
        if external_ref:
            existing = self.ledger.by_ref(external_ref)
            if existing:
                return {
                    "status": "replayed",
                    "transaction": existing,
                    "reply": f"Already recorded: {existing['id']}",
                }
        value = amount(amount_text)
        kind = normalize_type(type_text)
        day = iso_date(date_text, self.today)
        unit = (currency or self.settings.currency).upper()
        if len(unit) != 3 or not unit.isalpha():
            raise InputError("Currency must be a three-letter code")
        if kind == "transfer" and (not account or not to_account or account == to_account):
            raise InputError("Transfer needs distinct --account and --to-account")
        if category and category not in self.ledger.categories():
            raise InputError(f"Unknown category {category!r}; run categories or create-category")
        canonical = format(value.normalize(), "f")
        possible = [
            row
            for row in self.ledger.rows(
                "occurred_on=? AND type=? AND amount=? AND "
                "currency=? AND lower(merchant)=lower(?) AND payment_method=?",
                (day, kind, canonical, unit, merchant, payment_method),
            )
        ]
        receipt_id, receipt_path = self._receipt(receipt, day, merchant, canonical)
        if receipt_id:
            possible.extend(self.ledger.rows("receipt_id=?", (receipt_id,)))
        now = datetime.now(UTC).isoformat()
        row = {
            "id": uuid.uuid4().hex,
            "external_ref": external_ref,
            "occurred_on": day,
            "type": kind,
            "amount": canonical,
            "currency": unit,
            "merchant": merchant,
            "description": description,
            "category": category,
            "account": account,
            "to_account": to_account,
            "payment_method": payment_method,
            "notes": notes,
            "receipt_id": receipt_id,
            "receipt_path": receipt_path,
            "duplicate_status": "POSSIBLE_DUPLICATE" if possible else "",
            "reconciliation_status": "Pending",
            "reconciled_at": "",
            "created_at": now,
            "updated_at": now,
        }
        try:
            self.ledger.insert(row)
        except sqlite3.IntegrityError as exc:
            if external_ref and (existing := self.ledger.by_ref(external_ref)):
                return {
                    "status": "replayed",
                    "transaction": existing,
                    "reply": f"Already recorded: {existing['id']}",
                }
            raise InputError("Transaction could not be saved") from exc
        warning = " Possible duplicate; review it." if possible else ""
        return {
            "status": "created",
            "transaction": row,
            "possible_duplicate": bool(possible),
            "reply": f"Recorded {kind} {canonical} {unit} on {day}.{warning}",
        }

    def find(
        self,
        *,
        query: str = "",
        month_text: str | None = None,
        missing_receipt: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        if limit < 1 or limit > 500:
            raise InputError("Limit must be between 1 and 500")
        clauses: list[str] = []
        params: list[Any] = []
        if month_text:
            clauses.append("occurred_on LIKE ?")
            params.append(month(month_text, self.today) + "-%")
        if missing_receipt:
            clauses.append("type='expense' AND receipt_id='' ")
        if query:
            clauses.append("(merchant LIKE ? OR description LIKE ? OR notes LIKE ?)")
            params.extend([f"%{query}%"] * 3)
        rows = self.ledger.rows(" AND ".join(clauses), tuple(params))[:limit]
        return {"status": "ok", "items": rows, "reply": f"Found {len(rows)} transaction(s)."}

    def duplicates(self, month_text: str | None = None) -> dict[str, Any]:
        rows = self.find(month_text=month_text, limit=500)["items"]
        seen: set[tuple[str, ...]] = set()
        found = []
        for row in rows:
            key = (
                row["occurred_on"],
                row["type"],
                row["amount"],
                row["currency"],
                row["merchant"].casefold(),
                row["payment_method"],
            )
            if row["duplicate_status"] or key in seen:
                found.append(row)
            seen.add(key)
        return {
            "status": "ok",
            "items": found,
            "reply": f"Found {len(found)} possible duplicate(s).",
        }

    def summary(self, month_text: str | None = None) -> dict[str, Any]:
        target = month(month_text, self.today)
        rows = self.ledger.rows("occurred_on LIKE ?", (target + "-%",))
        totals: dict[str, dict[str, Decimal]] = {}
        for row in rows:
            bucket = totals.setdefault(
                row["currency"],
                {"income": Decimal(0), "expense": Decimal(0), "transfers": Decimal(0)},
            )
            value = Decimal(row["amount"])
            if row["type"] == "refund":
                bucket["expense"] -= value
            elif row["type"] == "transfer":
                bucket["transfers"] += value
            else:
                bucket[row["type"]] += value
        result = {
            unit: {
                **{key: str(value) for key, value in bucket.items()},
                "net": str(bucket["income"] - bucket["expense"]),
            }
            for unit, bucket in totals.items()
        }
        return {
            "status": "ok",
            "month": target,
            "totals": result,
            "count": len(rows),
            "reply": f"{target}: {len(rows)} transaction(s); totals by currency: {result}",
        }

    def reconcile(self, month_text: str) -> dict[str, Any]:
        target = month(month_text, self.today)
        rows = self.ledger.rows("occurred_on LIKE ?", (target + "-%",))
        now = datetime.now(UTC).isoformat()
        with self.ledger.connection:
            self.ledger.connection.execute(
                "UPDATE transactions SET reconciliation_status='Reconciled', "
                "reconciled_at=?, updated_at=? WHERE occurred_on LIKE ?",
                (self.today.isoformat(), now, target + "-%"),
            )
        return {
            "status": "reconciled",
            "month": target,
            "count": len(rows),
            "reply": f"Reconciled {len(rows)} transaction(s) for {target}.",
        }

    def attach(self, identifier: str, receipt: str) -> dict[str, Any]:
        row = self.ledger.get(identifier)
        if not row:
            raise InputError("Transaction not found")
        receipt_id, path = self._receipt(
            receipt, row["occurred_on"], row["merchant"], row["amount"]
        )
        self.ledger.update(
            identifier,
            {
                "receipt_id": receipt_id,
                "receipt_path": path,
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
        return {
            "status": "attached",
            "receipt_id": receipt_id,
            "reply": f"Receipt attached to {identifier}.",
        }

    def categories(self) -> dict[str, Any]:
        names = self.ledger.categories()
        return {"status": "ok", "items": names, "reply": ", ".join(names)}

    def create_category(self, name: str) -> dict[str, Any]:
        clean = name.strip()
        if not clean:
            raise InputError("Category name is required")
        self.ledger.add_category(clean)
        return {"status": "created", "category": clean, "reply": f"Category ready: {clean}"}
