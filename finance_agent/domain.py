"""Validation and finance semantics independent of storage or CLI."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

TYPES = ("expense", "income", "transfer", "refund")
DEFAULT_CATEGORIES = (
    "Food & Dining",
    "Transportation",
    "Housing",
    "Utilities",
    "Shopping",
    "Health",
    "Entertainment",
    "Education",
    "Travel",
    "Subscriptions",
    "Personal Care",
    "Gifts",
    "Salary",
    "Freelance",
    "Investment",
    "Other",
)


class InputError(ValueError):
    """A field needs correction before any write."""


def amount(value: str) -> Decimal:
    """Parse an explicit decimal amount. Never infer ambiguous locale separators."""
    raw = str(value).strip().replace(" ", "")
    if raw.startswith("Rp"):
        raw = raw[2:]
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", raw):
        raw = raw.replace(".", "")
    elif re.fullmatch(r"\d{1,3}(,\d{3})+", raw):
        raw = raw.replace(",", "")
    elif "," in raw and "." not in raw:
        raw = raw.replace(",", ".")
    try:
        result = Decimal(raw)
    except InvalidOperation as exc:
        raise InputError(f"Invalid amount: {value}") from exc
    if not result.is_finite() or result <= 0:
        raise InputError("Amount must be positive and finite")
    return result


def iso_date(value: str | None, today: date) -> str:
    if value is None:
        return today.isoformat()
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise InputError("Date must be YYYY-MM-DD") from exc


def month(value: str | None, today: date) -> str:
    result = value or today.strftime("%Y-%m")
    try:
        date.fromisoformat(result + "-01")
    except ValueError as exc:
        raise InputError("Month must be YYYY-MM") from exc
    return result


def normalize_type(value: str) -> str:
    result = value.lower()
    if result not in TYPES:
        raise InputError(f"Type must be one of: {', '.join(TYPES)}")
    return result
