"""One-time move of the Personal Finance Tracker ledger (Google Sheets) into doeedd.

Every ledger column is kept (decision D-02): the Sheets transaction id becomes ``external_ref``
``sheets:<id>``, the receipt becomes an attachment, reconciliation becomes the review flag and a
possible-duplicate flag stays a flag. Rows that cannot be mapped without guessing are skipped
and reported, never invented. Re-running is safe: rows whose ``external_ref`` already exists are
left alone, so a skipped row can be imported later once it is fixed in the sheet.
"""

from __future__ import annotations

import json
import mimetypes
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .aliases import resolve
from .capture import Names
from .client import DoeeddClient, DoeeddError
from .parsing import ParseError, parse_amount
from .receipts import CONFIG_PATH, drive_link

LEDGER_RANGE = "Transactions!A1:V5000"
REF_PREFIX = "sheets:"
KEY_PREFIX = "sheets-migration:"
SUSPICIOUS_AMOUNT = 1_000
TYPES = {"expense": "expense", "income": "income", "transfer": "transfer"}
_TRANSFER_NOTE = re.compile(r"(?P<source>[^>→]+?)\s*(?:->|→)\s*(?P<target>[^;,]+)")
_DRIVE_ID_IN_LINK = re.compile(r"/d/([A-Za-z0-9_-]+)")


@dataclass
class PlannedRow:
    """A ledger row ready to be written."""

    ref: str
    body: dict[str, Any]
    attachment: dict[str, Any] | None
    warnings: list[str] = field(default_factory=list)


@dataclass
class SkippedRow:
    """A ledger row that needs the owner's decision before it can be imported."""

    ref: str
    sheet_row: int
    reasons: list[str]


def ledger_spreadsheet_id(config_path: Path = CONFIG_PATH) -> str:
    """The ledger spreadsheet id persisted by ``build_finance_system.py``."""
    return json.loads(config_path.read_text(encoding="utf-8"))["spreadsheet_id"]


def read_ledger(sheets: Any, spreadsheet_id: str) -> list[dict[str, Any]]:
    """Real transaction rows of the Transactions tab, keyed by header, with their sheet row."""
    response = (
        sheets.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=LEDGER_RANGE,
            valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING",
        )
        .execute()
    )
    values = response.get("values", [])
    if not values:
        return []
    header = [str(name).strip() for name in values[0]]
    rows: list[dict[str, Any]] = []
    for sheet_row, raw in enumerate(values[1:], start=2):
        record = {name: raw[i] if i < len(raw) else "" for i, name in enumerate(header)}
        if _text(record.get("transaction_id")) or _text(record.get("transaction_date")):
            record["_sheet_row"] = sheet_row
            rows.append(record)
    return rows


def plan_migration(
    rows: list[dict[str, Any]], names: Names, aliases: dict[str, Any]
) -> tuple[list[PlannedRow], list[SkippedRow]]:
    """Map every ledger row to a doeedd transaction, or explain why it cannot be mapped."""
    planned: list[PlannedRow] = []
    skipped: list[SkippedRow] = []
    for row in rows:
        ref = _text(row.get("transaction_id")) or f"row-{row['_sheet_row']}"
        reasons: list[str] = []
        warnings: list[str] = []

        kind = TYPES.get(_text(row.get("transaction_type")).lower())
        if kind is None:
            reasons.append(
                f"type {_text(row.get('transaction_type'))!r} is not imported "
                "(refunds are applied to the original expense by hand)"
            )
        occurred_on = _date(row.get("transaction_date"), reasons)
        amount = _amount(row.get("amount"), reasons, warnings)
        currency = _text(row.get("currency")) or "IDR"
        if currency.upper() != "IDR":
            reasons.append(f"currency {currency} needs a manual conversion")

        category = None
        if kind in ("expense", "income"):
            wants_income = kind == "income"
            allowed = [c for c in names.categories if (c["kind"] == "income") == wants_income]
            category = _resolve(allowed, aliases["category"], row.get("category"))
            if category is None:
                reasons.append(f"category {_text(row.get('category'))!r} has no doeedd match")

        account = _resolve(names.accounts, aliases["account"], row.get("account")) or _resolve(
            names.accounts, aliases["account"], row.get("payment_method")
        )
        if account is None:
            reasons.append("no account: account and payment method are blank or unknown")

        target = None
        if kind == "transfer":
            match = _TRANSFER_NOTE.search(_text(row.get("notes")))
            if match:
                target = _resolve(names.accounts, aliases["account"], match.group("target"))
            if target is None:
                reasons.append("transfer destination not found in notes (expected 'A -> B')")

        if reasons:
            skipped.append(SkippedRow(ref, int(row["_sheet_row"]), reasons))
            continue
        assert occurred_on is not None and account is not None

        merchant = _text(row.get("merchant")) or None
        notes = "; ".join(
            part for part in (_text(row.get("notes")), f"[migrated from Sheets {ref}]") if part
        )
        source = _text(row.get("source"))
        if source and source != "Manual":
            notes += f" [source: {source}]"
        fallback = merchant or (category["name"] if category else "Transfer")
        body = {
            "occurred_on": occurred_on.isoformat(),
            "description": (_text(row.get("description")) or fallback)[:500],
            "type": kind,
            "category_id": category["id"] if category else None,
            "account_id": account["id"],
            "transfer_to_account_id": target["id"] if target else None,
            "amount": amount,
            "is_reviewed": _text(row.get("reconciliation_status")) == "Reconciled",
            "notes": notes[:2000],
            "merchant": merchant,
            "subcategory": _text(row.get("subcategory")) or None,
            "payment_method": _text(row.get("payment_method")) or None,
            "source": "import",
            "external_ref": f"{REF_PREFIX}{ref}",
            "possible_duplicate": _text(row.get("duplicate_status")) == "POSSIBLE_DUPLICATE",
        }
        planned.append(PlannedRow(ref, body, _attachment(row), warnings))
    return planned, skipped


def apply_migration(client: DoeeddClient, planned: list[PlannedRow]) -> dict[str, Any]:
    """Write planned rows that are not in doeedd yet, and link their receipts."""
    created: list[str] = []
    existing: list[str] = []
    linked = 0
    for row in planned:
        found = client.transactions(
            {"external_ref": row.body["external_ref"], "include_deleted": True}, max_items=1
        )
        if found:
            existing.append(row.ref)
            transaction = found[0]
        else:
            transaction, _ = client.create_transaction(
                row.body, idempotency_key=f"{KEY_PREFIX}{row.ref}"
            )
            created.append(row.ref)
        if row.attachment is not None:
            try:
                client.add_attachment(transaction["id"], row.attachment)
                linked += 1
            except DoeeddError as error:
                if error.kind != "conflict":
                    raise
    return {"created": created, "already_present": existing, "receipts_linked": linked}


def expense_total(rows: list[dict[str, Any]]) -> int:
    """Sum of readable expense amounts in ledger rows, for the after-migration comparison."""
    total = 0
    for row in rows:
        if _text(row.get("transaction_type")).lower() != "expense":
            continue
        amount = _amount(row.get("amount"), [], [])
        total += amount
    return total


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _resolve(
    items: list[dict[str, Any]], table: dict[str, Any], value: Any
) -> dict[str, Any] | None:
    text = _text(value)
    if not text:
        return None
    match = resolve(text, [item["name"] for item in items], table)
    return next((item for item in items if item["name"] == match.name), None)


def _date(value: Any, reasons: list[str]) -> date | None:
    text = _text(value)
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        reasons.append(f"date {text!r} is not YYYY-MM-DD")
        return None


def _amount(value: Any, reasons: list[str], warnings: list[str]) -> int:
    if isinstance(value, bool):
        reasons.append("amount is not a number")
        return 0
    if isinstance(value, int | float):
        if value != int(value):
            reasons.append(f"amount {value} is not a whole rupiah amount")
            return 0
        amount = int(value)
    else:
        try:
            amount = parse_amount(_text(value))
        except ParseError as exc:
            reasons.append(f"amount: {exc}")
            return 0
    if amount <= 0:
        reasons.append("amount must be greater than zero")
    elif amount < SUSPICIOUS_AMOUNT:
        warnings.append(f"amount {amount} is under Rp1.000; check it was not mis-parsed")
    return amount


def _attachment(row: dict[str, Any]) -> dict[str, Any] | None:
    file_id = _text(row.get("receipt_file_id"))
    if not file_id:
        link = _DRIVE_ID_IN_LINK.search(_text(row.get("receipt_link")))
        file_id = link.group(1) if link else ""
    if not file_id:
        return None
    filename = _text(row.get("receipt_filename")) or None
    return {
        "provider": "gdrive",
        "external_id": file_id,
        "url": drive_link(file_id),
        "filename": filename,
        "mime_type": mimetypes.guess_type(filename)[0] if filename else None,
    }
