import pytest

from etims import (
    RESULT_CODES,
    ConnectionFailedError,
    DeviceError,
    DuplicateError,
    EtimsError,
    KraError,
    KraServerError,
    RateLimitError,
    RequestError,
    ResponseTimeoutError,
    SequenceError,
)
from etims.errors import error_for


@pytest.mark.parametrize(
    ("code", "error_class"),
    [
        ("901", DeviceError),
        ("905", DeviceError),
        ("910", RequestError),
        ("921", DuplicateError),
        ("994", DuplicateError),
        ("922", SequenceError),
        ("990", RateLimitError),
        ("999", KraServerError),
    ],
)
def test_result_codes_map_to_exception_classes(code: str, error_class: type[KraError]) -> None:
    error = error_for(code, "message from KRA", response={"resultCd": code})
    assert type(error) is error_class
    assert isinstance(error, EtimsError)
    assert error.code == code
    assert error.message == "message from KRA"
    assert error.response == {"resultCd": code}
    assert str(error) == f"KRA {code}: message from KRA"


def test_unknown_codes_still_raise_kra_error() -> None:
    error = error_for("777", None)
    assert type(error) is KraError
    assert error.message == "Unknown result code"


def test_known_description_is_used_when_kra_sends_no_message() -> None:
    assert error_for("910", None).message == "Request parameter error"


def test_every_error_code_has_an_exception_class() -> None:
    for code, result in RESULT_CODES.items():
        assert (result.error is None) == (code in {"000", "001"}), code


def test_only_server_side_failures_are_retryable() -> None:
    assert error_for("990", None).retryable
    assert error_for("999", None).retryable
    assert not error_for("921", None).retryable
    assert not error_for("910", None).retryable


def test_transport_errors_say_whether_kra_may_have_the_request() -> None:
    assert ConnectionFailedError("refused").may_have_been_processed is False
    assert ResponseTimeoutError("no answer").may_have_been_processed is True
