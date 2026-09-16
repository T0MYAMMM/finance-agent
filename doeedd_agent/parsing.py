"""Amounts and dates the way people type them in chat, in Indonesian or English."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, tzinfo
from decimal import Decimal


class ParseError(ValueError):
    """The text is not a valid value."""


class AmbiguousError(ParseError):
    """The text could mean more than one value; the user has to say which."""


UNIT_MULTIPLIERS = {"k": 1_000, "rb": 1_000, "ribu": 1_000, "jt": 1_000_000, "juta": 1_000_000}
_AMOUNT = re.compile(r"(?P<number>\d+(?:[.,]\d+)*)(?P<unit>k|rb|ribu|jt|juta)?")
_SEPARATOR = re.compile(r"[.,]")


def parse_amount(text: str) -> int:
    """Parse a rupiah amount such as ``25rb``, ``1,5jt`` or ``Rp45.000`` into whole rupiah.

    A ``.`` or ``,`` followed by exactly three digits is a thousands separator. Any other
    separator marks decimals, which only make sense with a unit (``1,5jt``); ``45.5`` on its own
    is ambiguous. Zero and negative amounts are invalid.
    """
    cleaned = re.sub(r"\s+", "", text.strip().lower())
    if cleaned.startswith("-"):
        raise ParseError("the amount must be positive")
    for prefix in ("idr", "rp"):
        cleaned = cleaned.removeprefix(prefix)
    cleaned = cleaned.removeprefix(".").removesuffix(",-").removesuffix(".-")

    match = _AMOUNT.fullmatch(cleaned)
    if match is None:
        raise ParseError(f"not an amount: {text!r}")
    unit = match.group("unit")
    number = _number(match.group("number"), has_unit=unit is not None)
    value = number * UNIT_MULTIPLIERS.get(unit or "", 1)
    if value != value.to_integral_value():
        raise AmbiguousError(f"{text!r} is not a whole rupiah amount")
    amount = int(value)
    if amount <= 0:
        raise ParseError("the amount must be greater than zero")
    return amount


def _number(number: str, *, has_unit: bool) -> Decimal:
    """Interpret the digits and separators of an amount."""
    separators = _SEPARATOR.findall(number)
    if not separators:
        return Decimal(number)
    head, *rest = _SEPARATOR.split(number)

    if len(set(separators)) == 1 and all(len(group) == 3 for group in rest):
        if len(head) > 3:
            raise ParseError(f"not an amount: {number!r}")
        return Decimal(head + "".join(rest))

    *thousands, fraction = rest
    if separators[-1] in separators[:-1]:
        raise ParseError(f"not an amount: {number!r}")
    if thousands and (len(head) > 3 or any(len(group) != 3 for group in thousands)):
        raise ParseError(f"not an amount: {number!r}")
    value = Decimal(f"{head}{''.join(thousands)}.{fraction}")
    if not has_unit and value != value.to_integral_value():
        raise AmbiguousError(f"{number!r} has decimals; rupiah amounts are whole numbers")
    return value


RELATIVE_DAYS = {
    "today": 0,
    "now": 0,
    "hari ini": 0,
    "tadi": 0,
    "barusan": 0,
    "sekarang": 0,
    "yesterday": -1,
    "kemarin": -1,
    "kemaren": -1,
    "kemarin lusa": -2,
    "day before yesterday": -2,
}
WEEKDAYS = {
    "monday": 0,
    "senin": 0,
    "tuesday": 1,
    "selasa": 1,
    "wednesday": 2,
    "rabu": 2,
    "thursday": 3,
    "kamis": 3,
    "friday": 4,
    "jumat": 4,
    "jum'at": 4,
    "saturday": 5,
    "sabtu": 5,
    "sunday": 6,
    "minggu": 6,
}
MONTHS = {
    "jan": 1, "januari": 1, "january": 1,
    "feb": 2, "februari": 2, "february": 2,
    "mar": 3, "maret": 3, "march": 3,
    "apr": 4, "april": 4,
    "mei": 5, "may": 5,
    "jun": 6, "juni": 6, "june": 6,
    "jul": 7, "juli": 7, "july": 7,
    "agu": 8, "agt": 8, "agustus": 8, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "okt": 10, "oktober": 10, "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "des": 12, "desember": 12, "dec": 12, "december": 12,
}  # fmt: skip
_TIME = r"(?:[ ,t]+\d{1,2}[:.]\d{2}(?::\d{2})?)?"


def parse_date(text: str, today: date) -> date:
    """Parse ``kemarin``, ``senin lalu``, ``3/9``, ``14 Sep 2026``, ``2026-09-14`` and the like.

    Day-first order is used for numeric dates (Indonesian style). A date without a year that
    would land more than a day in the future is taken from the previous year.
    """
    t = " ".join(text.strip().lower().split())
    if not t:
        raise ParseError("empty date")
    if t in RELATIVE_DAYS:
        return today + timedelta(days=RELATIVE_DAYS[t])

    days_ago = re.fullmatch(r"(\d{1,3}) (?:hari (?:yang )?lalu|days? ago)", t)
    if days_ago:
        return today - timedelta(days=int(days_ago.group(1)))

    if t in ("minggu lalu", "last week"):
        raise AmbiguousError("'minggu lalu' means last week, not one day; which day?")
    weekday = re.sub(r"^(?:last|lalu) |(?: lalu| kemarin| kemaren)$", "", t)
    if weekday in WEEKDAYS:
        delta = (today.weekday() - WEEKDAYS[weekday]) % 7 or 7
        return today - timedelta(days=delta)

    iso = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})" + _TIME, t)
    if iso:
        return _build(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))

    numeric = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{4}|\d{2}))?" + _TIME, t)
    if numeric:
        day, month = int(numeric.group(1)), int(numeric.group(2))
        return _with_year(day, month, numeric.group(3), today)

    named = re.fullmatch(r"(\d{1,2}) ([a-z]+)\.?(?: (\d{4}))?" + _TIME, t)
    if named and named.group(2) in MONTHS:
        return _with_year(int(named.group(1)), MONTHS[named.group(2)], named.group(3), today)

    raise ParseError(f"not a date: {text!r}")


def _with_year(day: int, month: int, year_text: str | None, today: date) -> date:
    """Complete a day and month with the given year, or the most recent sensible one."""
    if year_text:
        year = int(year_text)
        return _build(year + 2000 if year < 100 else year, month, day)
    candidate = _build(today.year, month, day)
    if candidate > today + timedelta(days=1):
        candidate = _build(today.year - 1, month, day)
    return candidate


def _build(year: int, month: int, day: int) -> date:
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise ParseError(f"not a valid date: {year}-{month}-{day}") from exc


def today_in(timezone: tzinfo) -> date:
    """The calendar date right now in the user's timezone (never the server's)."""
    return datetime.now(timezone).date()
