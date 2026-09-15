"""Statement reconciliation: compare a bank or e-wallet statement with doeedd for one account.

The agent reads the statement (PDF or screenshot) and writes its lines to a small JSON file;
this module pairs each line with a doeedd movement of the same direction and amount within a
day, and lists what is missing on either side. It never writes: adding a missing line is an
ordinary ``add`` the owner confirms.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .parsing import ParseError, parse_amount, parse_date

DEBIT_WORDS = frozenset({"debit", "db", "d", "out", "keluar", "dr"})
CREDIT_WORDS = frozenset({"credit", "cr", "c", "in", "masuk", "kredit"})
TOLERANCE_DAYS = 1


@dataclass(frozen=True)
class StatementLine:
    """One movement on the statement, as the owner's bank shows it."""

    number: int
    occurred_on: date
    amount: int
    direction: str
    description: str = ""


@dataclass
class Reconciliation:
    """Statement lines paired with doeedd, and what is left over on each side."""

    matched: list[tuple[StatementLine, dict[str, Any]]] = field(default_factory=list)
    missing: list[StatementLine] = field(default_factory=list)
    extra: list[dict[str, Any]] = field(default_factory=list)


def load_statement(path: Path, today: date) -> list[StatementLine]:
    """Read ``[{"date", "amount", "direction", "description"}, ...]`` from a JSON file."""
    try:
        raw = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ParseError(f"cannot read statement file {path.name}: {exc}") from exc
    if not isinstance(raw, list) or not raw:
        raise ParseError("the statement file must be a non-empty JSON list of lines")
    lines = []
    for number, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ParseError(f"line {number}: expected an object")
        direction = str(item.get("direction", "")).strip().lower()
        if direction in DEBIT_WORDS:
            direction = "debit"
        elif direction in CREDIT_WORDS:
            direction = "credit"
        else:
            raise ParseError(f"line {number}: direction must be debit or credit")
        amount = item.get("amount")
        value = amount if isinstance(amount, int) and not isinstance(amount, bool) else None
        lines.append(
            StatementLine(
                number=number,
                occurred_on=parse_date(str(item.get("date", "")), today),
                amount=value if value is not None else parse_amount(str(amount)),
                direction=direction,
                description=str(item.get("description") or "").strip(),
            )
        )
    return lines


def movement(transaction: dict[str, Any], account_id: str) -> str | None:
    """Whether a transaction moved money out of (debit) or into (credit) the account."""
    kind = transaction["type"]
    if kind == "transfer":
        if transaction["account_id"] == account_id:
            return "debit"
        if transaction.get("transfer_to_account_id") == account_id:
            return "credit"
        return None
    if transaction["account_id"] != account_id:
        return None
    return "debit" if kind == "expense" else "credit"


def reconcile(
    lines: list[StatementLine],
    transactions: list[dict[str, Any]],
    account_id: str,
    tolerance_days: int = TOLERANCE_DAYS,
) -> Reconciliation:
    """Pair lines with doeedd movements (same direction and amount, closest date within range)."""
    candidates = [
        (item, direction)
        for item in transactions
        if item.get("deleted_at") is None and (direction := movement(item, account_id))
    ]
    used: set[str] = set()
    result = Reconciliation()
    for line in sorted(lines, key=lambda entry: (entry.occurred_on, entry.number)):
        best: dict[str, Any] | None = None
        best_gap = tolerance_days + 1
        for item, direction in candidates:
            if item["id"] in used or direction != line.direction or item["amount"] != line.amount:
                continue
            gap = abs((date.fromisoformat(item["occurred_on"]) - line.occurred_on).days)
            if gap <= tolerance_days and gap < best_gap:
                best, best_gap = item, gap
        if best is None:
            result.missing.append(line)
        else:
            used.add(best["id"])
            result.matched.append((line, best))
    result.extra = [item for item, _ in candidates if item["id"] not in used]
    return result


def statement_window(
    lines: list[StatementLine], tolerance_days: int = TOLERANCE_DAYS
) -> tuple[date, date]:
    """The date range of doeedd movements worth comparing with these lines."""
    start = min(line.occurred_on for line in lines) - timedelta(days=tolerance_days)
    end = max(line.occurred_on for line in lines) + timedelta(days=tolerance_days)
    return start, end
