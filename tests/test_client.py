from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

import pytest
import requests
import responses
from conftest import BASE, BHF, KEY, TIN, connection_refused, envelope, make_sale, sent_body, url

from etims import (
    EAT,
    ConfigurationError,
    ConnectionFailedError,
    DeviceError,
    DuplicateError,
    Environment,
    InvalidDataError,
    Item,
    KraServerError,
    OscuClient,
    ProductType,
    ProtocolError,
    RateLimitError,
    RequestError,
    ResponseTimeoutError,
    TaxType,
    TransportError,
)

RECEIPT = {
    "curRcptNo": 1,
    "totRcptNo": 1,
    "intrlData": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "rcptSign": "SIGN1234SIGN1234",
    "sdcDateTime": "20261014123010",
}


# -- credentials and request shape ------------------------------------------------


def test_credentials_go_in_headers_and_body(
    api: responses.RequestsMock, client: OscuClient
) -> None:
    api.post(url("/selectBhfList"), json=envelope({"bhfList": []}))
    client.get_branches(since=datetime(2026, 1, 1, tzinfo=EAT))
    request = api.calls[0].request
    assert request.headers["tin"] == TIN
    assert request.headers["bhfId"] == BHF
    assert request.headers["cmcKey"] == KEY
    assert request.headers["Content-Type"] == "application/json"
    assert sent_body(api.calls[0]) == {
        "tin": TIN,
        "bhfId": BHF,
        "cmcKey": KEY,
        "lastReqDt": "20260101000000",
    }


def test_lookups_default_to_a_full_sync(api: responses.RequestsMock, client: OscuClient) -> None:
    api.post(url("/selectNoticeList"), json=envelope({"noticeList": []}))
    client.get_notices()
    assert sent_body(api.calls[0])["lastReqDt"] == "20000101000000"


def test_lookup_results_and_sync_time(api: responses.RequestsMock, client: OscuClient) -> None:
    api.post(
        url("/selectCodeList"),
        json=envelope({"clsList": [{"cdCls": "17", "dtlList": [{"cd": "NT", "cdNm": "Net"}]}]}),
    )
    result = client.get_codes()
    assert result[0].class_id == "17"
    assert result[0].codes[0].name == "Net"
    assert result.as_of == datetime(2026, 10, 14, 12, 0, tzinfo=EAT)


def test_no_search_result_is_an_empty_list(api: responses.RequestsMock, client: OscuClient) -> None:
    api.post(
        url("/selectItemList"), json=envelope(None, code="001", message="There is no search result")
    )
    result = client.get_items()
    assert len(result) == 0
    assert result.as_of is not None


@pytest.mark.parametrize(
    ("method", "path", "key"),
    [
        ("get_item_classes", "/selectItemClsList", "itemClsList"),
        ("get_imported_items", "/selectImportItemList", "itemList"),
        ("get_purchase_sales", "/selectTrnsPurchaseSalesList", "saleList"),
        ("get_stock_movements", "/selectStockMoveList", "stockList"),
    ],
)
def test_every_lookup_reads_its_list(
    api: responses.RequestsMock, client: OscuClient, method: str, path: str, key: str
) -> None:
    api.post(url(path), json=envelope({key: [{}, {}]}))
    assert len(getattr(client, method)()) == 2


def test_taxpayer_lookup(api: responses.RequestsMock, client: OscuClient) -> None:
    api.post(
        url("/selectCustomer"),
        json=envelope(
            {"custList": [{"tin": "A123456789Z", "taxprNm": "Acme", "taxprSttsCd": "A"}]}
        ),
    )
    taxpayer = client.get_taxpayer("A123456789Z")
    assert taxpayer is not None
    assert taxpayer.is_active
    assert sent_body(api.calls[0])["custmTin"] == "A123456789Z"


def test_taxpayer_not_found(api: responses.RequestsMock, client: OscuClient) -> None:
    api.post(url("/selectCustomer"), json=envelope(None, code="001"))
    assert client.get_taxpayer("A123456789Z") is None


def test_taxpayer_pin_is_checked_locally(api: responses.RequestsMock, client: OscuClient) -> None:
    with pytest.raises(InvalidDataError):
        client.get_taxpayer("not-a-pin")
    assert len(api.calls) == 0


def test_save_item(api: responses.RequestsMock, client: OscuClient) -> None:
    api.post(url("/saveItem"), json=envelope(None))
    client.save_item(
        Item(
            item_code="KE3NTU0000001",
            item_class_code="8111180000",
            product_type=ProductType.SERVICE,
            name="Consulting",
            packaging_unit="NT",
            quantity_unit="U",
            tax_type=TaxType.STANDARD,
            default_price=Decimal("1160"),
        )
    )
    body = sent_body(api.calls[0])
    assert body["itemCd"] == "KE3NTU0000001"
    assert body["cmcKey"] == KEY


# -- sales ------------------------------------------------------------------------


def test_save_sale_returns_the_signature(api: responses.RequestsMock, client: OscuClient) -> None:
    api.post(url("/saveTrnsSalesOsdc"), json=envelope(RECEIPT))
    receipt = client.save_sale(make_sale(invoice_no=7))
    assert receipt.invoice_no == 7
    assert receipt.signature == "SIGN1234SIGN1234"
    assert receipt.qr_url is not None
    assert f"{TIN}00SIGN1234SIGN1234" in receipt.qr_url
    body = sent_body(api.calls[0])
    assert body["invcNo"] == 7
    assert body["tin"] == TIN


def test_accepted_sale_without_receipt_data_is_a_protocol_error(
    api: responses.RequestsMock, client: OscuClient
) -> None:
    api.post(url("/saveTrnsSalesOsdc"), json=envelope(None))
    with pytest.raises(ProtocolError) as info:
        client.save_sale(make_sale())
    assert info.value.may_have_been_processed


def test_submit_sale_needs_a_prepared_body(client: OscuClient) -> None:
    with pytest.raises(InvalidDataError, match="prepare_sale"):
        client.submit_sale({"trdInvcNo": "x"})


def test_kra_errors_become_typed_exceptions(
    api: responses.RequestsMock, client: OscuClient
) -> None:
    api.post(url("/saveTrnsSalesOsdc"), json=envelope(None, code="921", message="already declared"))
    with pytest.raises(DuplicateError) as info:
        client.save_sale(make_sale())
    assert info.value.code == "921"
    assert info.value.message == "already declared"
    assert info.value.result_dt == datetime(2026, 10, 14, 12, 0, tzinfo=EAT)
    assert len(api.calls) == 1


# -- retries ----------------------------------------------------------------------


def test_lookups_retry_transient_failures(api: responses.RequestsMock, client: OscuClient) -> None:
    api.post(url("/selectBhfList"), body=connection_refused())
    api.post(url("/selectBhfList"), body=requests.ReadTimeout("read timed out"))
    api.post(url("/selectBhfList"), json=envelope({"bhfList": [{"bhfId": "00"}]}))
    assert len(client.get_branches()) == 1
    assert len(api.calls) == 3


def test_lookups_give_up_after_the_retry_budget(
    api: responses.RequestsMock, client: OscuClient
) -> None:
    api.post(url("/selectBhfList"), body=requests.ReadTimeout("read timed out"))
    with pytest.raises(ResponseTimeoutError):
        client.get_branches()
    assert len(api.calls) == 3


def test_lookups_retry_server_errors(api: responses.RequestsMock, client: OscuClient) -> None:
    api.post(url("/selectCodeList"), status=503, body="Service Unavailable")
    api.post(url("/selectCodeList"), json=envelope(None, code="990"))
    api.post(url("/selectCodeList"), json=envelope({"clsList": []}))
    assert len(client.get_codes()) == 0
    assert len(api.calls) == 3


def test_lookups_do_not_retry_rejections(api: responses.RequestsMock, client: OscuClient) -> None:
    api.post(
        url("/selectCodeList"), json=envelope(None, code="910", message="Request parameter error")
    )
    with pytest.raises(RequestError):
        client.get_codes()
    assert len(api.calls) == 1


def test_saves_retry_only_when_nothing_was_sent(
    api: responses.RequestsMock, client: OscuClient
) -> None:
    api.post(url("/saveTrnsSalesOsdc"), body=connection_refused())
    api.post(url("/saveTrnsSalesOsdc"), json=envelope(RECEIPT))
    assert client.save_sale(make_sale()).signature == "SIGN1234SIGN1234"
    assert len(api.calls) == 2


def test_saves_are_never_resent_after_a_timeout(
    api: responses.RequestsMock, client: OscuClient
) -> None:
    api.post(url("/saveTrnsSalesOsdc"), body=requests.ReadTimeout("read timed out"))
    with pytest.raises(ResponseTimeoutError) as info:
        client.save_sale(make_sale())
    assert info.value.may_have_been_processed
    assert len(api.calls) == 1


def test_connection_lost_mid_request_is_ambiguous(
    api: responses.RequestsMock, client: OscuClient
) -> None:
    api.post(url("/saveTrnsSalesOsdc"), body=requests.ConnectionError("Connection aborted"))
    with pytest.raises(TransportError) as info:
        client.save_sale(make_sale())
    assert info.value.may_have_been_processed
    assert len(api.calls) == 1


def test_saves_are_not_retried_on_server_errors(
    api: responses.RequestsMock, client: OscuClient
) -> None:
    api.post(url("/saveTrnsSalesOsdc"), json=envelope(None, code="999"))
    with pytest.raises(KraServerError):
        client.save_sale(make_sale())
    assert len(api.calls) == 1


def test_unreachable_kra(api: responses.RequestsMock, client: OscuClient) -> None:
    api.post(url("/saveTrnsSalesOsdc"), body=connection_refused())
    with pytest.raises(ConnectionFailedError) as info:
        client.save_sale(make_sale())
    assert not info.value.may_have_been_processed
    assert len(api.calls) == 3


def test_connect_timeout_means_nothing_was_sent(
    api: responses.RequestsMock, client: OscuClient
) -> None:
    api.post(url("/saveItem"), body=requests.ConnectTimeout("connect timed out"))
    with pytest.raises(ConnectionFailedError):
        client._save("/saveItem", {})
    assert len(api.calls) == 3


@pytest.mark.parametrize(
    ("status", "processed"),
    [(404, False), (502, True)],
)
def test_http_errors(
    api: responses.RequestsMock, client: OscuClient, status: int, processed: bool
) -> None:
    api.post(url("/saveItem"), status=status, body="<html>error</html>")
    with pytest.raises(ProtocolError) as info:
        client._save("/saveItem", {})
    assert info.value.status_code == status
    assert info.value.may_have_been_processed is processed
    assert info.value.body == "<html>error</html>"


@pytest.mark.parametrize("body", ["not json", json.dumps([1, 2]), json.dumps({"data": {}})])
def test_malformed_answers(api: responses.RequestsMock, client: OscuClient, body: str) -> None:
    api.post(url("/saveItem"), body=body)
    with pytest.raises(ProtocolError):
        client._save("/saveItem", {})


def test_other_request_failures(api: responses.RequestsMock, client: OscuClient) -> None:
    api.post(url("/saveItem"), body=requests.exceptions.ChunkedEncodingError("broken"))
    with pytest.raises(TransportError) as info:
        client._save("/saveItem", {})
    assert info.value.may_have_been_processed


def test_rate_limit_error_class(api: responses.RequestsMock, client: OscuClient) -> None:
    api.post(url("/selectCodeList"), json=envelope(None, code="990"))
    with pytest.raises(RateLimitError):
        client.get_codes()


# -- device initialization ----------------------------------------------------------


def test_initialize_device(api: responses.RequestsMock) -> None:
    api.post(
        url("/selectInitOsdcInfo"),
        json=envelope({"info": {"tin": TIN, "bhfId": BHF, "dvcId": "1", "cmcKey": "new-key"}}),
    )
    device = OscuClient.initialize_device(TIN, BHF, "dvc-0001")
    assert device.cmc_key == "new-key"
    request = api.calls[0].request
    assert "cmcKey" not in request.headers
    assert sent_body(api.calls[0]) == {"tin": TIN, "bhfId": BHF, "dvcSrlNo": "dvc-0001"}


def test_initialize_device_not_approved_for_oscu(api: responses.RequestsMock) -> None:
    # What the live sandbox returns for a serial that isn't an approved OSCU device.
    api.post(
        url("/selectInitOsdcInfo"),
        json=envelope(None, code="905", message="Only OSCU device can be verified."),
    )
    with pytest.raises(DeviceError, match="905"):
        OscuClient.initialize_device(TIN, BHF, "dvc-0001")


@pytest.mark.parametrize("data", [None, {"info": None}, {"info": {"tin": TIN}}])
def test_initialize_device_without_a_key(api: responses.RequestsMock, data: object) -> None:
    api.post(url("/selectInitOsdcInfo"), json=envelope(data))
    with pytest.raises(ProtocolError):
        OscuClient.initialize_device(TIN, BHF, "dvc-0001")


def test_initialize_device_checks_its_input(api: responses.RequestsMock) -> None:
    with pytest.raises(ConfigurationError):
        OscuClient.initialize_device("bad", BHF, "dvc")
    with pytest.raises(ConfigurationError):
        OscuClient.initialize_device(TIN, BHF, "")
    assert len(api.calls) == 0


# -- configuration ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tin", "bhf", "key"),
    [("bad", "00", KEY), (TIN, "0", KEY), (TIN, "AB", KEY), (TIN, "00", "")],
)
def test_constructor_validation(tin: str, bhf: str, key: str) -> None:
    with pytest.raises(ConfigurationError):
        OscuClient(tin, bhf, key)


def test_repr_hides_the_key() -> None:
    client = OscuClient(TIN, BHF, KEY)
    assert KEY not in repr(client)
    assert "sandbox" in repr(client)


def test_from_env() -> None:
    client = OscuClient.from_env(
        environ={
            "OSCU_TIN": TIN,
            "OSCU_BHF_ID": "01",
            "OSCU_CMC_KEY": KEY,
            "OSCU_ENVIRONMENT": "production",
        }
    )
    assert client.bhf_id == "01"
    assert client.environment is Environment.PRODUCTION
    with pytest.raises(ConfigurationError, match="OSCU_CMC_KEY"):
        OscuClient.from_env(environ={"OSCU_TIN": TIN, "OSCU_BHF_ID": "00"})


def test_base_url_override(api: responses.RequestsMock) -> None:
    api.post("http://localhost:8088/selectBhfList", json=envelope({"bhfList": []}))
    OscuClient(TIN, BHF, KEY, base_url="http://localhost:8088/").get_branches()
    assert len(api.calls) == 1


def test_partial_tax_rates_are_merged_with_defaults() -> None:
    client = OscuClient(TIN, BHF, KEY, tax_rates={TaxType.STANDARD: Decimal(14)})
    assert client.tax_rates[TaxType.STANDARD] == Decimal(14)
    assert client.tax_rates[TaxType.REDUCED] == Decimal(8)


def test_production_urls() -> None:
    assert Environment.PRODUCTION.api_url == "https://etims-api.kra.go.ke/etims-api"
    assert Environment.SANDBOX.api_url == BASE
