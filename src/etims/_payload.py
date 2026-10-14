"""Building request bodies (with KRA's field rules) and reading responses."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, TypeVar

from ._util import (
    Number,
    from_yes_no,
    json_number,
    kra_date,
    kra_datetime,
    parse_kra_date,
    parse_kra_datetime,
    to_decimal,
    yes_no,
)
from .errors import InvalidDataError

E = TypeVar("E", bound=Enum)

_TWO_PLACES = Decimal("0.01")


@dataclass(frozen=True)
class Operator:
    """Who registers or changes data at KRA (regrId/regrNm, modrId/modrNm).

    Usually your system or the logged-in user. ``id`` is at most 20 characters
    and ``name`` at most 60.
    """

    id: str
    name: str


class Body:
    """Builds a KRA request body, enforcing the specification's field rules.

    Values that would be truncated or rounded are refused with
    ``InvalidDataError`` instead, so nothing is changed silently.
    """

    def __init__(self, context: str) -> None:
        self._context = context
        self._data: dict[str, Any] = {}

    def _fail(self, key: str, problem: str) -> InvalidDataError:
        return InvalidDataError(f"{self._context}.{key} {problem}")

    def text(self, key: str, value: str | None, max_length: int, *, required: bool = False) -> Body:
        if value is None or value == "":
            if required:
                raise self._fail(key, "is required")
            self._data[key] = None
            return self
        if not isinstance(value, str):
            raise self._fail(key, f"must be text, not {type(value).__name__}")
        if len(value) > max_length:
            raise self._fail(key, f"must be at most {max_length} characters (got {len(value)})")
        self._data[key] = value
        return self

    def code(self, key: str, value: Enum | str | None, *, required: bool = False) -> Body:
        raw = value.value if isinstance(value, Enum) else value
        return self.text(key, raw, 5, required=required)

    def integer(
        self,
        key: str,
        value: int | None,
        *,
        required: bool = False,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> Body:
        if value is None:
            if required:
                raise self._fail(key, "is required")
            self._data[key] = None
            return self
        if isinstance(value, bool) or not isinstance(value, int):
            raise self._fail(key, "must be a whole number")
        if minimum is not None and value < minimum:
            raise self._fail(key, f"must be at least {minimum}")
        if maximum is not None and value > maximum:
            raise self._fail(key, f"must be at most {maximum}")
        self._data[key] = value
        return self

    def amount(self, key: str, value: Number | None, *, required: bool = False) -> Body:
        """A NUMBER(n,2) field: at most two decimal places."""
        if value is None:
            if required:
                raise self._fail(key, "is required")
            self._data[key] = None
            return self
        try:
            number = to_decimal(value)
            exact = number == number.quantize(_TWO_PLACES)
        except (ValueError, InvalidOperation) as exc:
            raise self._fail(key, f"must be a number (got {value!r})") from exc
        if not exact:
            raise self._fail(key, f"must have at most 2 decimal places (got {value})")
        self._data[key] = json_number(number)
        return self

    def flag(self, key: str, value: bool) -> Body:
        self._data[key] = yes_no(value)
        return self

    def timestamp(self, key: str, value: datetime | None, *, required: bool = False) -> Body:
        if value is None:
            if required:
                raise self._fail(key, "is required")
            self._data[key] = None
            return self
        self._data[key] = kra_datetime(value)
        return self

    def day(self, key: str, value: date | None, *, required: bool = False) -> Body:
        if value is None:
            if required:
                raise self._fail(key, "is required")
            self._data[key] = None
            return self
        self._data[key] = kra_date(value)
        return self

    def operator(self, operator: Operator, *, registrant: bool = True) -> Body:
        """Add modrId/modrNm, and regrId/regrNm unless ``registrant`` is False."""
        if registrant:
            self.text("regrId", operator.id, 20, required=True)
            self.text("regrNm", operator.name, 60, required=True)
        self.text("modrId", operator.id, 20, required=True)
        self.text("modrNm", operator.name, 60, required=True)
        return self

    def set(self, key: str, value: Any) -> Body:
        self._data[key] = value
        return self

    def build(self) -> dict[str, Any]:
        return dict(self._data)


class Record:
    """Typed, forgiving access to one object in a KRA response.

    Keys are stripped (the spec's samples contain keys like ``"curRcptNo "``)
    and malformed values read as None instead of raising.
    """

    def __init__(self, data: Mapping[str, Any]) -> None:
        self.raw: dict[str, Any] = {str(k).strip(): v for k, v in data.items()}

    def _get(self, *keys: str) -> Any:
        for key in keys:
            value = self.raw.get(key)
            if value is not None:
                return value
        return None

    def text(self, *keys: str) -> str | None:
        value = self._get(*keys)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def integer(self, *keys: str) -> int | None:
        value = self._get(*keys)
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(str(value).strip())
        except ValueError:
            return None

    def decimal(self, *keys: str) -> Decimal | None:
        value = self._get(*keys)
        if value is None or isinstance(value, bool):
            return None
        try:
            return to_decimal(value if isinstance(value, (int, float)) else str(value).strip())
        except (ValueError, InvalidOperation):
            return None

    def timestamp(self, *keys: str) -> datetime | None:
        return parse_kra_datetime(self.text(*keys))

    def day(self, *keys: str) -> date | None:
        return parse_kra_date(self.text(*keys))

    def flag(self, *keys: str) -> bool | None:
        return from_yes_no(self.text(*keys))

    def enum(self, enum_class: type[E], *keys: str) -> E | None:
        value = self.text(*keys)
        if value is None:
            return None
        try:
            return enum_class(value)
        except ValueError:
            return None

    def records(self, *keys: str) -> list[Record]:
        value = self._get(*keys)
        if not isinstance(value, list):
            return []
        return [Record(item) for item in value if isinstance(item, Mapping)]
