"""End-to-end tests: the real client over real HTTP against the fake KRA server."""

from __future__ import annotations

import time
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
import requests
from conftest import make_sale

from etims import (
    DEFAULT_TAX_RATES,
    CreditNoteReason,
    DeviceError,
    DuplicateError,
    Item,
    OscuClient,
    OutboxStatus,
    ProductType,
    ProtocolError,
    ReceiptType,
    RequestError,
    RetryPolicy,
    SaleOutbox,
    TaxType,
    tax_rates_from_codes,
)
from etims.testing import FakeKra

TIN = "P051234567A"

ITEM = Item(
    item_code="KE2NTU0000001",  # the item make_sale() sells
    item_class_code="5059690800",
    product_type=ProductType.FINISHED_PRODUCT,
    name="Integration consulting",
    packaging_unit="NT",
    quantity_unit="U",
    tax_type=TaxType.STANDARD,
    default_price=Decimal("580.00"),
)


@pytest.fixture
def kra() -> Iterator[FakeKra]:
    with FakeKra() as fake:
        yield fake


def connect(kra: FakeKra, **kwargs: object) -> OscuClient:
    device = OscuClient.initialize_device(TIN, "00", "serial-1", base_url=kra.url)
    assert device.cmc_key
    options: dict[str, object] = {
        "base_url": kra.url,
        "retry": RetryPolicy(attempts=2),
        "sleep": lambda _: None,
        "timeout": (2.0, 2.0),
    }
    options.update(kwargs)
    return OscuClient(TIN, "00", device.cmc_key, **options)  # type: ignore[arg-type]


@pytest.fixture
def client(kra: FakeKra) -> Iterator[OscuClient]:
    with connect(kra) as c:
        yield c


def test_device_initialization_gives_a_stable_key(kra: FakeKra) -> None:
    first = OscuClient.initialize_device(TIN, "00", "serial-1", base_url=kra.url)
    second = OscuClient.initialize_device(TIN, "00", "serial-1", base_url=kra.url)
    assert first.cmc_key == second.cmc_key
    assert first.is_head_office is True
    assert first.sdc_id is not None


def test_unapproved_serial_gets_the_live_sandbox_error() -> None:
    with FakeKra(approved_serials={"approved"}) as kra, pytest.raises(DeviceError, match="905"):
        OscuClient.initialize_device(TIN, "00", "other", base_url=kra.url)


def test_wrong_key_is_refused(kra: FakeKra) -> None:
    OscuClient.initialize_device(TIN, "00", "serial-1", base_url=kra.url)
    with (
        OscuClient(TIN, "00", "wrong-key", base_url=kra.url) as client,
        pytest.raises(DeviceError, match="901"),
    ):
        client.get_branches()


def test_missing_headers_and_bad_requests(kra: FakeKra) -> None:
    no_headers = requests.post(f"{kra.url}/selectCodeList", json={"lastReqDt": "20000101000000"})
    assert no_headers.json()["resultCd"] == "900"
    not_json = requests.post(f"{kra.url}/selectCodeList", data=b"not json")
    assert not_json.json()["resultCd"] == "911"
    assert requests.post(f"{kra.url}/noSuchEndpoint", json={}).status_code == 404


def test_reference_lookups(client: OscuClient) -> None:
    assert tax_rates_from_codes(client.get_codes()) == dict(DEFAULT_TAX_RATES)
    assert len(client.get_item_classes()) == 5
    assert client.get_branches()[0].branch_id == "00"
    taxpayer = client.get_taxpayer("A123456789Z")
    assert taxpayer is not None
    assert taxpayer.is_active
    assert len(client.get_notices()) == 0


def test_sale_and_credit_note(kra: FakeKra, client: OscuClient) -> None:
    client.save_item(ITEM)
    assert len(client.get_items()) == 1

    sale = client.save_sale(make_sale())
    assert sale.signature is not None
    assert len(sale.signature) == 16
    assert (sale.receipt_no, sale.total_receipt_no) == (1, 1)
    assert sale.qr_url is not None

    note = client.save_sale(
        make_sale(
            invoice_no=2,
            receipt_type=ReceiptType.CREDIT_NOTE,
            original_invoice_no=1,
            credit_note_reason=CreditNoteReason.REFUND,
        )
    )
    assert (note.receipt_no, note.total_receipt_no) == (1, 2)  # first credit note, second receipt
    recorded = kra.sales(TIN, "00")
    assert recorded[1]["receipt"]["rcptSign"] == sale.signature
    assert recorded[2]["request"]["rcptTyCd"] == "R"


def test_items_must_be_registered_before_sale(client: OscuClient) -> None:
    with pytest.raises(RequestError, match="not registered"):
        client.save_sale(make_sale())


def test_invoice_numbers_must_be_sequential(client: OscuClient) -> None:
    client.save_item(ITEM)
    with pytest.raises(RequestError, match="out of sequence; expected 1"):
        client.save_sale(make_sale(invoice_no=2))


def test_invoice_numbers_cannot_be_reused(client: OscuClient) -> None:
    client.save_item(ITEM)
    client.save_sale(make_sale())
    with pytest.raises(DuplicateError):
        client.save_sale(make_sale())


def test_totals_are_checked(client: OscuClient) -> None:
    client.save_item(ITEM)
    body = client.prepare_sale(make_sale())
    body["taxAmtB"] = 999
    with pytest.raises(RequestError, match="taxAmtB does not match"):
        client.submit_sale(body)


def test_credit_note_needs_a_known_original(client: OscuClient) -> None:
    client.save_item(ITEM)
    with pytest.raises(RequestError, match="unknown invoice 7"):
        client.save_sale(
            make_sale(
                receipt_type=ReceiptType.CREDIT_NOTE,
                original_invoice_no=7,
                credit_note_reason=CreditNoteReason.REFUND,
            )
        )


def test_simulated_kra_error(kra: FakeKra, client: OscuClient) -> None:
    kra.fail_next("/selectBhfList", http_status=503, times=2)
    with pytest.raises(ProtocolError):
        client.get_branches()  # both attempts fail
    assert len(client.get_branches()) == 1


def test_outbox_rides_out_outages(tmp_path: Path, kra: FakeKra, client: OscuClient) -> None:
    client.save_item(ITEM)
    with SaleOutbox(tmp_path / "outbox.sqlite3", client) as outbox:
        for _ in range(3):
            outbox.enqueue(make_sale(invoice_no=None))

        kra.fail_next("/saveTrnsSalesOsdc", http_status=503)
        assert outbox.flush().uncertain == [1]  # KRA failed before recording it

        kra.fail_next("/saveTrnsSalesOsdc", drop=True)
        assert outbox.flush().uncertain == [1]  # connection dropped, no answer

        report = outbox.flush()
        assert report.sent == [1, 2, 3]
        assert sorted(kra.sales(TIN, "00")) == [1, 2, 3]


def test_outbox_detects_a_sale_whose_answer_was_lost(tmp_path: Path, kra: FakeKra) -> None:
    with connect(kra, timeout=(2.0, 0.3)) as client:
        client.save_item(ITEM)
        with SaleOutbox(tmp_path / "outbox.sqlite3", client) as outbox:
            outbox.enqueue(make_sale(invoice_no=None))
            outbox.enqueue(make_sale(invoice_no=None))

            # KRA records invoice 1, but only after the client stopped waiting.
            kra.fail_next("/saveTrnsSalesOsdc", delay=0.8)
            assert outbox.flush().uncertain == [1]
            deadline = time.monotonic() + 5
            while 1 not in kra.sales(TIN, "00") and time.monotonic() < deadline:
                time.sleep(0.05)

            report = outbox.flush()
            assert report.duplicates == [1]  # the resend proved KRA already had it
            assert report.sent == [2]
            entry = outbox.get(1)
            assert entry is not None
            assert entry.status is OutboxStatus.DUPLICATE


def test_state_survives_a_restart(tmp_path: Path) -> None:
    state = tmp_path / "kra.json"
    with FakeKra(state_file=state) as kra, connect(kra) as client:
        client.save_item(ITEM)
        client.save_sale(make_sale())
    with FakeKra(state_file=state) as kra, connect(kra) as client:
        assert len(client.get_items()) == 1
        assert client.save_sale(make_sale(invoice_no=2)).total_receipt_no == 2


def test_lenient_mode_skips_the_extra_checks() -> None:
    with FakeKra(strict=False) as kra, connect(kra) as client:
        assert client.save_sale(make_sale(invoice_no=5)).signature  # unregistered, out of order
