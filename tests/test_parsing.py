from datetime import date

import pytest

from doeedd_agent.parsing import AmbiguousError, ParseError, parse_amount, parse_date

TODAY = date(2026, 9, 15)  # a Tuesday


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("25k", 25_000),
        ("25rb", 25_000),
        ("25 ribu", 25_000),
        ("1,5jt", 1_500_000),
        ("1.5jt", 1_500_000),
        ("1,5 juta", 1_500_000),
        ("Rp45.000", 45_000),
        ("Rp 45.000", 45_000),
        ("Rp. 27.600", 27_600),
        ("Rp45.000,-", 45_000),
        ("45.000", 45_000),
        ("45,000", 45_000),
        ("1.250.000", 1_250_000),
        ("45.000,00", 45_000),
        ("16200", 16_200),
        ("2.5k", 2_500),
        ("IDR 90.544", 90_544),
    ],
)
def test_parses_rupiah_amounts(text: str, expected: int) -> None:
    assert parse_amount(text) == expected


@pytest.mark.parametrize("text", ["45.5", "12,50", "1.000.000,50"])
def test_decimals_without_a_unit_are_ambiguous(text: str) -> None:
    with pytest.raises(AmbiguousError):
        parse_amount(text)


@pytest.mark.parametrize("text", ["0", "-5000", "abc", "1.2.3", "1000.000", "", "rp"])
def test_rejects_things_that_are_not_amounts(text: str) -> None:
    with pytest.raises(ParseError):
        parse_amount(text)


def test_finance_py_regression_dot_thousands_is_not_a_decimal() -> None:
    # finance.py parsed "16.200" as 16.2; the skill had to strip dots by hand.
    assert parse_amount("16.200") == 16_200


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("hari ini", date(2026, 9, 15)),
        ("tadi", date(2026, 9, 15)),
        ("kemarin", date(2026, 9, 14)),
        ("yesterday", date(2026, 9, 14)),
        ("kemarin lusa", date(2026, 9, 13)),
        ("3 hari lalu", date(2026, 9, 12)),
        ("senin", date(2026, 9, 14)),
        ("senin lalu", date(2026, 9, 14)),
        ("selasa", date(2026, 9, 8)),
        ("last friday", date(2026, 9, 11)),
        ("3/9", date(2026, 9, 3)),
        ("14/09/26 12:31", date(2026, 9, 14)),
        ("14-09-2026", date(2026, 9, 14)),
        ("2026-09-14", date(2026, 9, 14)),
        ("14 Sep 2026", date(2026, 9, 14)),
        ("1 agustus", date(2026, 8, 1)),
        ("28/12", date(2025, 12, 28)),
    ],
)
def test_parses_dates(text: str, expected: date) -> None:
    assert parse_date(text, TODAY) == expected


def test_last_week_is_not_a_day() -> None:
    with pytest.raises(AmbiguousError):
        parse_date("minggu lalu", TODAY)


@pytest.mark.parametrize("text", ["31/02", "someday", "", "13 smarch"])
def test_rejects_invalid_dates(text: str) -> None:
    with pytest.raises(ParseError):
        parse_date(text, TODAY)
