from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from etims import is_valid_pin
from etims._util import (
    EAT,
    json_number,
    kra_date,
    kra_datetime,
    money,
    parse_kra_date,
    parse_kra_datetime,
    to_decimal,
)


def test_money_rounds_half_up_to_cents() -> None:
    assert money("2.675") == Decimal("2.68")
    assert money(Decimal("1601.685")) == Decimal("1601.69")
    assert money("2.674") == Decimal("2.67")
    assert money(10) == Decimal("10.00")


def test_floats_convert_without_binary_noise() -> None:
    assert to_decimal(0.1) == Decimal("0.1")
    assert money(0.1 + 0.2) == Decimal("0.30")


def test_to_decimal_rejects_non_numbers() -> None:
    with pytest.raises(ValueError, match="Not a number"):
        to_decimal("abc")


def test_json_number_keeps_whole_numbers_as_integers() -> None:
    assert json_number(Decimal("3500.00")) == 3500
    assert isinstance(json_number(Decimal("3500.00")), int)
    assert json_number(Decimal("10.50")) == 10.5


def test_kra_datetime_uses_kenyan_time() -> None:
    assert kra_datetime(datetime(2026, 10, 14, 9, 5, 7)) == "20261014090507"  # naive = Kenyan time
    utc = datetime(2026, 10, 14, 9, 5, 7, tzinfo=timezone.utc)
    assert kra_datetime(utc) == "20261014120507"


def test_kra_date_converts_aware_datetimes_to_kenyan_date() -> None:
    late_utc = datetime(2026, 12, 31, 22, 30, tzinfo=timezone.utc)  # already 1 Jan in Nairobi
    assert kra_date(late_utc) == "20270101"
    assert kra_date(date(2026, 3, 9)) == "20260309"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("20261014120507", datetime(2026, 10, 14, 12, 5, 7, tzinfo=EAT)),
        ("2026-10-14 12:05:07", datetime(2026, 10, 14, 12, 5, 7, tzinfo=EAT)),
        ("-1   ", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_kra_datetime(text: str | None, expected: datetime | None) -> None:
    assert parse_kra_datetime(text) == expected


def test_parse_kra_date() -> None:
    assert parse_kra_date("20260309") == date(2026, 3, 9)
    assert parse_kra_date("2026-03-09") is None
    assert parse_kra_date(None) is None


@pytest.mark.parametrize(
    ("pin", "valid"),
    [
        ("P051234567A", True),
        ("A123456789Z", True),
        ("P05123456A", False),
        ("p051234567a", False),
        ("PP51234567A", False),
        ("", False),
    ],
)
def test_pin_validation(pin: str, valid: bool) -> None:
    assert is_valid_pin(pin) is valid
