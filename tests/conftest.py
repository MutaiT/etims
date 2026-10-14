from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
import requests
import responses
from urllib3.exceptions import MaxRetryError, NewConnectionError

from etims import EAT, OscuClient, RetryPolicy, Sale, SaleLine, TaxType

TIN = "P051234567A"
BHF = "00"
KEY = "0123456789abcdef0123456789abcdef"
BASE = "https://etims-api-sbx.kra.go.ke/etims-api"


def url(path: str) -> str:
    return BASE + path


def envelope(
    data: Any = None, code: str = "000", message: str = "It is succeeded"
) -> dict[str, Any]:
    return {"resultCd": code, "resultMsg": message, "resultDt": "20261014120000", "data": data}


def connection_refused() -> requests.ConnectionError:
    """The error requests raises when the TCP connection cannot be made."""
    cause = NewConnectionError(None, "Failed to establish a new connection: Connection refused")  # type: ignore[arg-type]
    return requests.ConnectionError(MaxRetryError(None, BASE, reason=cause))  # type: ignore[arg-type]


def sent_body(call: Any) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(call.request.body)
    return body


@pytest.fixture
def api() -> Iterator[responses.RequestsMock]:
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        yield mock


@pytest.fixture
def client() -> Iterator[OscuClient]:
    with OscuClient(TIN, BHF, KEY, retry=RetryPolicy(attempts=3), sleep=lambda _: None) as c:
        yield c


def make_sale(**overrides: Any) -> Sale:
    fields: dict[str, Any] = {
        "invoice_no": 1,
        "trader_invoice_no": "INV/2026/0001",
        "confirmed_at": datetime(2026, 10, 14, 12, 30, 5, tzinfo=EAT),
        "lines": [
            SaleLine(
                item_code="KE2NTU0000001",
                item_class_code="5059690800",
                name="Integration consulting",
                quantity=2,
                unit_price=Decimal("580.00"),
                tax_type=TaxType.STANDARD,
            )
        ],
    }
    fields.update(overrides)
    return Sale(**fields)
