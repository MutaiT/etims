"""Internal helpers: Kenyan time, KRA date formats, money arithmetic."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

#: Kenya uses East Africa Time (UTC+3) all year, with no daylight saving.
#: A fixed offset avoids depending on the tz database (missing on Windows).
EAT = timezone(timedelta(hours=3), "EAT")

Number = Decimal | int | float | str

_CENT = Decimal("0.01")
_PIN = re.compile(r"^[A-Z]\d{9}[A-Z]$")


def to_decimal(value: Number) -> Decimal:
    """Convert to Decimal without binary float noise (0.1 stays 0.1)."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(repr(value))
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"Not a number: {value!r}") from exc


def money(value: Number) -> Decimal:
    """Round to cents, half up. KRA amounts are NUMBER(18,2)."""
    return to_decimal(value).quantize(_CENT, rounding=ROUND_HALF_UP)


def json_number(value: Decimal) -> int | float:
    """Decimal to a JSON number. Whole numbers stay integers, as in KRA's samples."""
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def kra_datetime(value: datetime) -> str:
    """Format as yyyyMMddHHmmss in Kenyan time. Naive datetimes are taken as Kenyan time."""
    if value.tzinfo is not None:
        value = value.astimezone(EAT)
    return value.strftime("%Y%m%d%H%M%S")


def kra_date(value: date) -> str:
    """Format as yyyyMMdd. Aware datetimes are converted to Kenyan time first."""
    if isinstance(value, datetime) and value.tzinfo is not None:
        value = value.astimezone(EAT)
    return value.strftime("%Y%m%d")


def parse_kra_datetime(text: str | None) -> datetime | None:
    """Parse KRA timestamps into aware EAT datetimes.

    Accepts yyyyMMddHHmmss, and the 'YYYY-MM-DD HH:MM:SS' form the purchase
    list uses. Returns None for empty or malformed values, which do occur in
    KRA responses (the spec's own import sample has a date of "-1").
    """
    if not text or not text.strip():
        return None
    text = text.strip()
    for fmt in ("%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=EAT)
        except ValueError:
            continue
    return None


def parse_kra_date(text: str | None) -> date | None:
    """Parse yyyyMMdd. Returns None for empty or malformed values."""
    if not text or not text.strip():
        return None
    try:
        return datetime.strptime(text.strip(), "%Y%m%d").date()
    except ValueError:
        return None


def now_eat() -> datetime:
    return datetime.now(EAT)


def is_valid_pin(pin: str) -> bool:
    """True for a well-formed KRA PIN: a letter, nine digits, a letter (P051234567A)."""
    return bool(_PIN.match(pin))


def yes_no(flag: bool) -> str:
    return "Y" if flag else "N"


def from_yes_no(value: object) -> bool | None:
    if value == "Y":
        return True
    if value == "N":
        return False
    return None
