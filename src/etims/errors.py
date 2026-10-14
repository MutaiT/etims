"""Exceptions, and the KRA result codes they map to.

Every error this package raises derives from ``EtimsError``:

    EtimsError
    ├── ConfigurationError     bad local setup (e.g. malformed PIN, missing cmcKey)
    ├── InvalidDataError       data fails KRA's rules before anything is sent
    ├── TransportError         the network failed
    │   ├── ConnectionFailedError   never reached KRA, always safe to retry
    │   └── ResponseTimeoutError    sent, but no answer: KRA may have recorded it
    ├── ProtocolError          KRA answered with something that isn't a valid response
    └── KraError               KRA answered with an error result code
        ├── AuthenticationError     900
        ├── DeviceError             901, 902, 903, 905
        ├── RequestError            910, 911, 912
        ├── DuplicateError          921, 994
        ├── SequenceError           922
        └── KraServerError          990-999 (RateLimitError for 990)

Transport and protocol errors carry ``may_have_been_processed``. When it is
True for a sale, resend the *same* invoice (same invoice number): if KRA had
already recorded it, the resend fails with ``DuplicateError`` rather than
creating a second invoice.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any


class EtimsError(Exception):
    """Base class for every error raised by this package."""


class ConfigurationError(EtimsError):
    """The client is set up wrongly (malformed PIN, missing communication key...)."""


class InvalidDataError(EtimsError, ValueError):
    """The data breaks one of KRA's rules, so it was not sent."""


class TransportError(EtimsError):
    """The request could not be completed over the network."""

    def __init__(self, message: str, *, may_have_been_processed: bool) -> None:
        super().__init__(message)
        self.may_have_been_processed = may_have_been_processed


class ConnectionFailedError(TransportError):
    """No connection to KRA was made, so nothing was sent. Always safe to retry."""

    def __init__(self, message: str) -> None:
        super().__init__(message, may_have_been_processed=False)


class ResponseTimeoutError(TransportError):
    """The request was sent but KRA did not answer in time. It may have been recorded."""

    def __init__(self, message: str) -> None:
        super().__init__(message, may_have_been_processed=True)


class ProtocolError(EtimsError):
    """KRA's answer was not a valid API response (HTTP error page, invalid JSON...)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: str | None = None,
        may_have_been_processed: bool = True,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body
        self.may_have_been_processed = may_have_been_processed


class KraError(EtimsError):
    """KRA processed the request and returned an error result code."""

    #: Whether retrying the identical *read* request may succeed.
    #: Write requests are never retried automatically.
    retryable: bool = False

    def __init__(
        self,
        code: str,
        message: str,
        *,
        result_dt: datetime | None = None,
        response: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(f"KRA {code}: {message}")
        self.code = code
        self.message = message
        self.result_dt = result_dt
        self.response = response or {}


class AuthenticationError(KraError):
    """The tin, bhfId or cmcKey headers are missing or not accepted."""


class DeviceError(KraError):
    """The device serial or branch is not set up for OSCU, or is already installed."""


class RequestError(KraError):
    """KRA rejected the request content (missing or invalid parameters)."""


class DuplicateError(KraError):
    """KRA already has this record (e.g. an invoice number that was sent before)."""


class SequenceError(KraError):
    """Data arrived out of order (e.g. an invoice before its sale)."""


class KraServerError(KraError):
    """KRA failed internally while handling the request. Lookups may be retried."""

    retryable = True


class RateLimitError(KraServerError):
    """Too many lookups. Retrying later may succeed."""


@dataclass(frozen=True)
class ResultCode:
    code: str
    description: str
    error: type[KraError] | None
    note: str = ""


#: Result codes from section 4.18 of the OSCU specification v2.0, plus codes
#: observed from the live API. ``error`` is None for the two success codes.
RESULT_CODES: Mapping[str, ResultCode] = {
    rc.code: rc
    for rc in (
        ResultCode("000", "It is succeeded", None),
        ResultCode("001", "There is no search result", None, "Treated as an empty list"),
        ResultCode("891", "An error occurred while Request URL is created", KraError),
        ResultCode("892", "An error occurred while Request Header data is created", KraError),
        ResultCode("893", "An error occurred while Request Body data is created", KraError),
        ResultCode("894", "An error regarding server communication occurred", KraError),
        ResultCode("895", "An error regarding unallowed Request Method occurred", KraError),
        ResultCode("896", "An error regarding Request Status occurred", KraError),
        ResultCode("899", "An error regarding Client occurred", KraError),
        ResultCode("900", "There is no Header information", AuthenticationError),
        ResultCode("901", "It is not valid device", DeviceError),
        ResultCode("902", "This device is installed", DeviceError),
        ResultCode("903", "Only OSCU device can be verified", DeviceError),
        ResultCode(
            "905",
            "Only OSCU device can be verified",
            DeviceError,
            "Not in the spec; returned by the live sandbox for a serial that is not "
            "approved as an OSCU device",
        ),
        ResultCode("910", "Request parameter error", RequestError),
        ResultCode("911", "There is no request full text", RequestError),
        ResultCode("912", "There is a request Method error", RequestError),
        ResultCode(
            "921",
            "Sales or sales invoice data which is declared cannot be received",
            DuplicateError,
        ),
        ResultCode(
            "922",
            "Sales invoice data can be received after receiving the sales data",
            SequenceError,
        ),
        ResultCode("990", "The maximum number of views are exceeded", RateLimitError),
        ResultCode("991", "There is an error during registration", KraServerError),
        ResultCode("992", "There is an error during modification", KraServerError),
        ResultCode("993", "There is an error during deletion", KraServerError),
        ResultCode("994", "There is an overlapped Data", DuplicateError),
        ResultCode("995", "There is no downloaded file", KraServerError),
        ResultCode("999", "There is an unknown error", KraServerError),
    )
}


def error_for(
    code: str,
    message: str | None,
    *,
    result_dt: datetime | None = None,
    response: Mapping[str, Any] | None = None,
) -> KraError:
    """Build the right exception for a KRA error result code."""
    known = RESULT_CODES.get(code)
    error_class = known.error if known and known.error else KraError
    text = message or (known.description if known else "Unknown result code")
    return error_class(code, text, result_dt=result_dt, response=response)
