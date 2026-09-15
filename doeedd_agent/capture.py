"""Recording money movements in doeedd on the user's behalf.

:class:`Recorder` resolves the user's words to doeedd ids with the alias memory, refuses to
guess what it cannot resolve, checks for a likely duplicate, uploads a receipt before anything
is written, creates the transaction with an idempotency key, links the receipt, and queues the
write locally when doeedd cannot be reached. Nothing here computes totals: usage comes from
doeedd's monthly report.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .aliases import AliasStore, normalize, resolve
from .client import DoeeddClient, DoeeddError
from .formatting import idr, percent, short_date
from .outbox import Outbox
from .receipts import drive_link
from .state import StateStore

log = logging.getLogger(__name__)

TRANSACTION_TYPES = ("expense", "income", "transfer")
QUEUEABLE_ERRORS = frozenset({"unreachable", "server", "rate_limited"})
DESCRIPTION_MAX = 500

Uploader = Callable[[Path, date, "str | None", int, "str | None"], dict[str, Any]]


@dataclass
class CaptureRequest:
    """One money movement as the agent understood it; names, not ids."""

    amount: int
    occurred_on: date
    type: str = "expense"
    category: str | None = None
    account: str | None = None
    to_account: str | None = None
    merchant: str | None = None
    description: str | None = None
    subcategory: str | None = None
    payment_method: str | None = None
    notes: str | None = None
    key: str | None = None
    receipt_path: Path | None = None
    receipt_file_id: str | None = None
    reviewed: bool = False
    allow_duplicate: bool = False
    allow_future: bool = False


@dataclass
class EditRequest:
    """Fields to change on an existing transaction; ``None`` leaves a field untouched."""

    amount: int | None = None
    occurred_on: date | None = None
    category: str | None = None
    account: str | None = None
    to_account: str | None = None
    merchant: str | None = None
    description: str | None = None
    subcategory: str | None = None
    payment_method: str | None = None
    notes: str | None = None
    reviewed: bool | None = None
    learn: bool = False


@dataclass
class Outcome:
    """What happened, in a shape the skill can branch on (``status``) and relay (``reply``)."""

    status: str
    reply: str
    transaction: dict[str, Any] | None = None
    transactions: list[dict[str, Any]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    options: dict[str, list[str]] = field(default_factory=dict)
    duplicates: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    receipt: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly form, without empty fields."""
        return {key: value for key, value in asdict(self).items() if value not in (None, [], {})}


class Names:
    """doeedd categories and accounts, looked up by id or name."""

    def __init__(self, categories: list[dict[str, Any]], accounts: list[dict[str, Any]]) -> None:
        self.categories = categories
        self.accounts = accounts
        self._by_id = {item["id"]: item for item in [*categories, *accounts]}

    def name(self, item_id: str | None) -> str | None:
        """Display name for a category or account id."""
        item = self._by_id.get(item_id or "")
        return item["name"] if item else None

    def category(self, item_id: str | None) -> dict[str, Any] | None:
        """The category with this id."""
        item = self._by_id.get(item_id or "")
        return item if item and "kind" in item else None

    def compact(self, transaction: dict[str, Any]) -> dict[str, Any]:
        """A short, name-resolved view of a transaction for chat."""
        return {
            "id": transaction["id"],
            "date": transaction["occurred_on"],
            "type": transaction["type"],
            "amount": transaction["amount"],
            "category": self.name(transaction.get("category_id")),
            "account": self.name(transaction.get("account_id")),
            "to_account": self.name(transaction.get("transfer_to_account_id")),
            "merchant": transaction.get("merchant"),
            "description": transaction.get("description"),
            "payment_method": transaction.get("payment_method"),
            "source": transaction.get("source"),
            "reviewed": transaction.get("is_reviewed"),
            "deleted": transaction.get("deleted_at") is not None,
        }


class Recorder:
    """Creates, corrects, removes and restores transactions for the agent."""

    def __init__(
        self,
        client: DoeeddClient,
        aliases: AliasStore,
        state: StateStore,
        outbox: Outbox,
        uploader: Uploader | None = None,
    ) -> None:
        self.client = client
        self.aliases = aliases
        self.state = state
        self.outbox = outbox
        self.uploader = uploader

    def names(self) -> Names:
        """Current doeedd categories and accounts (active only)."""
        return Names(self.client.categories(), self.client.accounts())

    # -- capture ----------------------------------------------------------------------------

    def capture(self, request: CaptureRequest, today: date) -> Outcome:
        """Record one transaction, or say what is missing, duplicated or queued."""
        if request.type not in TRANSACTION_TYPES:
            raise ValueError(f"type must be one of {', '.join(TRANSACTION_TYPES)}")
        if request.key:
            existing = self._find_by_ref(request.key)
            if existing is not None:
                return self._recorded(existing, self.names(), replayed=True)
        key = request.key or f"agent:{uuid.uuid4()}"

        names = self.names()
        memory = self.aliases.merged()
        self._fill_from_merchant(request, memory)
        category, account, to_account, missing, options = self._resolve(request, names, memory)
        if request.occurred_on > today and not request.allow_future:
            missing.append("date")
        if missing:
            return Outcome(
                "needs_input",
                reply=f"Need {', '.join(missing)} to log {idr(request.amount)}.",
                missing=missing,
                options=options,
            )
        assert account is not None  # resolved above, or reported as missing

        duplicates = self._duplicates(request, account["id"])
        if duplicates and not request.allow_duplicate:
            first = duplicates[0]
            return Outcome(
                "possible_duplicate",
                reply=(
                    f"Looks like {idr(first['amount'])} on {first['occurred_on']} is already "
                    "logged. Log it again?"
                ),
                duplicates=[names.compact(item) for item in duplicates],
            )

        receipt = self._receipt(request, category)
        body = {
            "occurred_on": request.occurred_on.isoformat(),
            "description": _description(request, category),
            "type": request.type,
            "category_id": category["id"] if category else None,
            "account_id": account["id"],
            "transfer_to_account_id": to_account["id"] if to_account else None,
            "amount": request.amount,
            "is_reviewed": request.reviewed,
            "notes": request.notes,
            "merchant": request.merchant,
            "subcategory": request.subcategory,
            "payment_method": request.payment_method,
            "source": "agent",
            "external_ref": key,
            "possible_duplicate": bool(duplicates),
        }
        attachment = _attachment_body(receipt)

        try:
            transaction, replayed = self.client.create_transaction(body, idempotency_key=key)
        except DoeeddError as error:
            if error.kind in QUEUEABLE_ERRORS:
                self.outbox.append(
                    {"kind": "capture", "key": key, "body": body, "attachment": attachment}
                )
                return Outcome(
                    "queued",
                    reply="⏸️ Couldn't reach doeedd. Saved it and I'll retry; nothing was lost.",
                    receipt=receipt,
                )
            if error.kind == "conflict":
                existing = self._find_by_ref(key)
                if existing is not None:
                    return self._recorded(existing, names, replayed=True)
            raise

        if attachment is not None:
            self._attach(transaction["id"], attachment)
        self.state.record_capture(key, transaction["id"])
        if category is not None:
            self.state.remember_account(category["name"], account["name"])
        return self._recorded(transaction, names, replayed=replayed, receipt=receipt)

    def _resolve(
        self, request: CaptureRequest, names: Names, memory: dict[str, Any]
    ) -> tuple[
        dict[str, Any] | None,
        dict[str, Any] | None,
        dict[str, Any] | None,
        list[str],
        dict[str, list[str]],
    ]:
        """Map category and account words to doeedd items; list what could not be mapped."""
        missing: list[str] = []
        options: dict[str, list[str]] = {}
        category = None
        if request.type != "transfer":
            wants_income = request.type == "income"
            allowed = [c for c in names.categories if (c["kind"] == "income") == wants_income]
            category = _pick(allowed, memory["category"], request.category or request.merchant)
            if category is None:
                missing.append("category")
                options["category"] = [item["name"] for item in allowed]

        account = self._pick_account(request, names.accounts, memory, category)
        to_account = None
        if account is None:
            missing.append("account")
        if request.type == "transfer":
            to_account = _pick(names.accounts, memory["account"], request.to_account)
            if to_account is None or account is not None and to_account["id"] == account["id"]:
                missing.append("to_account")
        if {"account", "to_account"} & set(missing):
            options["account"] = [item["name"] for item in names.accounts]
        return category, account, to_account, missing, options

    def _pick_account(
        self,
        request: CaptureRequest,
        accounts: list[dict[str, Any]],
        memory: dict[str, Any],
        category: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Explicit account, then payment method, then the category's usual account, then default.

        Transfers never fall back: moving money between the user's own accounts must name both.
        """
        if request.account:
            return _pick(accounts, memory["account"], request.account)
        if request.type == "transfer":
            return None
        if request.payment_method:
            found = _pick(accounts, memory["account"], request.payment_method)
            if found is not None:
                return found
        fallbacks = [
            self.state.account_for_category(category["name"]) if category else None,
            memory.get("default_account"),
        ]
        for name in fallbacks:
            found = _pick(accounts, {}, name)
            if found is not None:
                return found
        return None

    def _fill_from_merchant(self, request: CaptureRequest, memory: dict[str, Any]) -> None:
        """Use what the merchant usually is (category, account, subcategory, payment method)."""
        if not request.merchant:
            return
        defaults = memory["merchant_defaults"].get(normalize(request.merchant), {})
        for name in ("category", "account", "subcategory", "payment_method"):
            if getattr(request, name) is None and defaults.get(name):
                setattr(request, name, defaults[name])

    def _duplicates(self, request: CaptureRequest, account_id: str) -> list[dict[str, Any]]:
        """Same amount and account within a day either side, with a similar name (if any)."""
        window = self.client.transactions(
            {
                "from": request.occurred_on - timedelta(days=1),
                "to": request.occurred_on + timedelta(days=1),
                "type": request.type,
            },
            max_items=200,
        )
        return [
            item
            for item in window
            if item["amount"] == request.amount
            and account_id in (item["account_id"], item.get("transfer_to_account_id"))
            and _similar_names(item, request)
        ]

    def _receipt(
        self, request: CaptureRequest, category: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Upload the receipt before recording, so a failed upload records nothing."""
        if request.receipt_path is not None:
            if self.uploader is None:
                raise RuntimeError("receipt uploads are not configured")
            return self.uploader(
                request.receipt_path,
                request.occurred_on,
                request.merchant,
                request.amount,
                category["name"] if category else None,
            )
        if request.receipt_file_id:
            file_id = request.receipt_file_id
            return {"file_id": file_id, "url": drive_link(file_id), "name": None, "mime_type": None}
        return None

    def _attach(self, transaction_id: str, attachment: dict[str, Any]) -> None:
        """Link a receipt; queue the link if doeedd is unreachable, ignore an existing link."""
        try:
            self.client.add_attachment(transaction_id, attachment)
        except DoeeddError as error:
            if error.kind == "conflict":
                return
            if error.kind in QUEUEABLE_ERRORS:
                self.outbox.append(
                    {
                        "kind": "attachment",
                        "transaction_id": transaction_id,
                        "attachment": attachment,
                    }
                )
                return
            raise

    def _find_by_ref(self, key: str) -> dict[str, Any] | None:
        """The live transaction recorded with this client reference, if any."""
        found = self.client.transactions({"external_ref": key}, max_items=1)
        return found[0] if found else None

    def _recorded(
        self,
        transaction: dict[str, Any],
        names: Names,
        *,
        replayed: bool,
        receipt: dict[str, Any] | None = None,
    ) -> Outcome:
        usage = None if replayed else self._usage(transaction, names)
        return Outcome(
            "replayed" if replayed else "created",
            reply=_capture_reply(transaction, names, usage, replayed=replayed, receipt=receipt),
            transaction=names.compact(transaction),
            usage=usage,
            receipt=receipt,
        )

    def _usage(self, transaction: dict[str, Any], names: Names) -> dict[str, Any] | None:
        """This month's plan versus actual for the transaction's category, from doeedd."""
        category = names.category(transaction.get("category_id"))
        if transaction["type"] != "expense" or category is None:
            return None
        if category["kind"] not in ("need", "want"):
            return None
        occurred = date.fromisoformat(transaction["occurred_on"])
        try:
            report = self.client.report_monthly(occurred.year, occurred.month)
        except DoeeddError as error:
            log.warning("usage lookup failed: %s", error.kind)
            return None
        row = next((r for r in report["categories"] if r["category"]["id"] == category["id"]), None)
        if row is None:
            return None
        return {
            "category": category["name"],
            "planned": row["planned"],
            "actual": row["actual"],
            "usage": row["usage"],
            "status": row["status"],
        }

    # -- corrections --------------------------------------------------------------------------

    def target_ids(self, transaction_id: str | None) -> list[str]:
        """An explicit id, or the entries of the last capture."""
        return [transaction_id] if transaction_id else self.state.last_capture_ids()

    def edit(self, transaction_id: str | None, change: EditRequest) -> Outcome:
        """Correct one transaction; with ``learn``, remember the merchant's category/account."""
        ids = self.target_ids(transaction_id)
        if len(ids) != 1:
            reason = "nothing logged yet" if not ids else "the last message logged several entries"
            return Outcome(
                "needs_input", reply=f"Which entry? ({reason}; pass --id)", missing=["id"]
            )
        current = self.client.get_transaction(ids[0])
        names = self.names()
        memory = self.aliases.merged()
        body: dict[str, Any] = {}
        missing: list[str] = []
        options: dict[str, list[str]] = {}

        if change.category is not None:
            wants_income = current["type"] == "income"
            allowed = [c for c in names.categories if (c["kind"] == "income") == wants_income]
            category = _pick(allowed, memory["category"], change.category)
            if category is None:
                missing.append("category")
                options["category"] = [item["name"] for item in allowed]
            else:
                body["category_id"] = category["id"]
        for field_name, api_field in (
            ("account", "account_id"),
            ("to_account", "transfer_to_account_id"),
        ):
            text = getattr(change, field_name)
            if text is None:
                continue
            account = _pick(names.accounts, memory["account"], text)
            if account is None:
                missing.append(field_name)
                options["account"] = [item["name"] for item in names.accounts]
            else:
                body[api_field] = account["id"]
        if missing:
            return Outcome(
                "needs_input", reply=f"Need {', '.join(missing)}.", missing=missing, options=options
            )

        simple = {
            "amount": change.amount,
            "occurred_on": change.occurred_on.isoformat() if change.occurred_on else None,
            "merchant": change.merchant,
            "description": change.description,
            "subcategory": change.subcategory,
            "payment_method": change.payment_method,
            "notes": change.notes,
            "is_reviewed": change.reviewed,
        }
        body.update({key: value for key, value in simple.items() if value is not None})
        if not body:
            return Outcome("needs_input", reply="Nothing to change.", missing=["change"])

        updated = self.client.update_transaction(current["id"], body)
        merchant = updated.get("merchant")
        if change.learn and merchant and ({"category_id", "account_id"} & set(body)):
            self.aliases.remember_merchant(
                merchant,
                category=names.name(updated.get("category_id")) if "category_id" in body else None,
                account=names.name(updated.get("account_id")) if "account_id" in body else None,
            )
        compact = names.compact(updated)
        return Outcome(
            "updated",
            reply=f"✏️ Updated: {_line(compact)}",
            transaction=compact,
        )

    def undo(self, transaction_id: str | None = None) -> Outcome:
        """Soft-delete the last capture (or one id); restorable for 30 days."""
        ids = self.target_ids(transaction_id)
        if not ids:
            return Outcome("nothing", reply="Nothing to undo.")
        for item_id in ids:
            self.client.delete_transaction(item_id)
        self.state.record_deleted(ids)
        entries = "entry" if len(ids) == 1 else "entries"
        return Outcome(
            "deleted", reply=f'🗑️ Removed {len(ids)} {entries}. Reply "restore" to bring it back.'
        )

    def restore(self) -> Outcome:
        """Bring back what the last undo removed."""
        ids = self.state.take_deleted_ids()
        if not ids:
            return Outcome("nothing", reply="Nothing to restore.")
        names = self.names()
        restored = [names.compact(self.client.restore_transaction(item_id)) for item_id in ids]
        return Outcome(
            "restored",
            reply="↩️ Restored: " + "; ".join(_line(item) for item in restored),
            transactions=restored,
        )

    def attach(
        self, transaction_id: str | None, receipt_path: Path | None, receipt_file_id: str | None
    ) -> Outcome:
        """Add a receipt to an existing transaction (the last capture by default)."""
        ids = self.target_ids(transaction_id)
        if len(ids) != 1:
            return Outcome(
                "needs_input", reply="Which entry gets the receipt? (pass --id)", missing=["id"]
            )
        transaction = self.client.get_transaction(ids[0])
        names = self.names()
        request = CaptureRequest(
            amount=transaction["amount"],
            occurred_on=date.fromisoformat(transaction["occurred_on"]),
            merchant=transaction.get("merchant"),
            receipt_path=receipt_path,
            receipt_file_id=receipt_file_id,
        )
        receipt = self._receipt(request, names.category(transaction.get("category_id")))
        attachment = _attachment_body(receipt)
        if attachment is None:
            return Outcome("needs_input", reply="Send the receipt file.", missing=["receipt"])
        self._attach(transaction["id"], attachment)
        return Outcome(
            "attached",
            reply=f"📎 Receipt linked to {_line(names.compact(transaction))}",
            transaction=names.compact(transaction),
            receipt=receipt,
        )

    def flush(self) -> Outcome:
        """Send queued writes; stop at the first that still cannot reach doeedd."""
        sent = 0
        failed = 0
        for entry in self.outbox.entries():
            try:
                if entry["kind"] == "capture":
                    try:
                        transaction, _ = self.client.create_transaction(
                            entry["body"], idempotency_key=entry["key"]
                        )
                    except DoeeddError as error:
                        existing = (
                            self._find_by_ref(entry["key"]) if error.kind == "conflict" else None
                        )
                        if existing is None:
                            raise
                        transaction = existing
                    if entry.get("attachment"):
                        self._attach(transaction["id"], entry["attachment"])
                    self.state.record_capture(entry["key"], transaction["id"])
                elif entry["kind"] == "attachment":
                    self._attach(entry["transaction_id"], entry["attachment"])
            except DoeeddError as error:
                if error.kind in QUEUEABLE_ERRORS:
                    break
                self.outbox.fail(entry, error.to_dict())
                failed += 1
                continue
            self.outbox.remove(entry["id"])
            sent += 1
        remaining = len(self.outbox)
        reply = f"Sent {sent} queued write(s); {remaining} still waiting"
        if failed:
            reply += f"; {failed} rejected by doeedd (see outbox.failed.json)"
        return Outcome("flushed", reply=reply + ".")


def _pick(
    items: list[dict[str, Any]], table: dict[str, Any], text: str | None
) -> dict[str, Any] | None:
    """The item a word refers to, or ``None`` when it is unknown or ambiguous."""
    if not text:
        return None
    match = resolve(text, [item["name"] for item in items], table)
    if match.name is None:
        return None
    return next(item for item in items if item["name"] == match.name)


def _similar_names(existing: dict[str, Any], request: CaptureRequest) -> bool:
    """True when either side has no name, or one name contains the other."""
    theirs = {normalize(v) for v in (existing.get("merchant"), existing.get("description")) if v}
    ours = {normalize(v) for v in (request.merchant, request.description) if v}
    if not theirs or not ours:
        return True
    return any(a in b or b in a for a in theirs for b in ours)


def _description(request: CaptureRequest, category: dict[str, Any] | None) -> str:
    """doeedd requires a description; fall back to the merchant or the category."""
    text = request.description or request.merchant or (category["name"] if category else "Transfer")
    return text[:DESCRIPTION_MAX]


def _attachment_body(receipt: dict[str, Any] | None) -> dict[str, Any] | None:
    if receipt is None:
        return None
    return {
        "provider": "gdrive",
        "external_id": receipt["file_id"],
        "url": receipt["url"],
        "filename": receipt.get("name"),
        "mime_type": receipt.get("mime_type"),
    }


def _line(item: dict[str, Any]) -> str:
    """``Rp45.000 · Food · BCA · 10 Sep — Starbucks``."""
    when = short_date(date.fromisoformat(item["date"]))
    if item["type"] == "transfer":
        return f"{idr(item['amount'])} {item['account']} → {item['to_account']} · {when}"
    text = f"{idr(item['amount'])} · {item['category']} · {item['account']} · {when}"
    label = item.get("merchant") or item.get("description")
    return f"{text} — {label}" if label and label != item["category"] else text


def _capture_reply(
    transaction: dict[str, Any],
    names: Names,
    usage: dict[str, Any] | None,
    *,
    replayed: bool,
    receipt: dict[str, Any] | None,
) -> str:
    item = names.compact(transaction)
    if item["type"] == "transfer":
        first = f"🔁 {_line(item)} · not counted as spending"
    else:
        first = f"✅ {_line(item)}"
    if replayed:
        first = f"↩️ Already logged: {_line(item)}"
    lines = [first + (" 📎" if receipt else "")]
    if usage and usage["planned"]:
        marker = {"over": " ⚠️ over budget", "warn": " ⚠️"}.get(usage["status"] or "", "")
        lines.append(f"{usage['category']} {percent(usage['usage'])} of budget{marker}")
    if not replayed:
        lines.append('Reply "undo" to remove.')
    return "\n".join(lines)
