"""Short, chat-friendly renderings of money, ratios and dates."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

MONTH_ABBREVIATIONS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)  # fmt: skip


def idr(amount: int) -> str:
    """``45000`` -> ``Rp45.000``; negative values keep their sign."""
    sign = "-" if amount < 0 else ""
    return f"{sign}Rp{abs(amount):,}".replace(",", ".")


def idr_short(amount: int) -> str:
    """Conversational rupiah: ``Rp1,5jt`` from a million up, otherwise :func:`idr`."""
    if abs(amount) < 1_000_000:
        return idr(amount)
    millions = (Decimal(abs(amount)) / Decimal(1_000_000)).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )
    text = f"{millions:f}".rstrip("0").rstrip(".").replace(".", ",")
    sign = "-" if amount < 0 else ""
    return f"{sign}Rp{text}jt"


def percent(ratio: float | None) -> str:
    """``0.684`` -> ``68%``; ``None`` (nothing planned) -> ``-``."""
    return "-" if ratio is None else f"{round(ratio * 100)}%"


def short_date(value: date) -> str:
    """``date(2026, 9, 10)`` -> ``10 Sep``."""
    return f"{value.day} {MONTH_ABBREVIATIONS[value.month - 1]}"
