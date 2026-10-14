from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
import requests
import responses
from conftest import connection_refused, envelope, make_sale, sent_body, url

from etims import InvalidDataError, OscuClient, OutboxStatus, SaleOutbox

SALES = url("/saveTrnsSalesOsdc")


def receipt(n: int) -> dict[str, object]:
    return {"curRcptNo": n, "totRcptNo": n, "intrlData": "DATA", "rcptSign": f"SIGN{n:012d}"}


@pytest.fixture
def outbox(tmp_path: Path, client: OscuClient) -> Iterator[SaleOutbox]:
    with SaleOutbox(tmp_path / "etims.sqlite3", client) as box:
        yield box


def test_invoice_numbers_are_sequential(outbox: SaleOutbox) -> None:
    assert outbox.last_invoice_no == 0
    assert [outbox.enqueue(make_sale(invoice_no=None)) for _ in range(3)] == [1, 2, 3]
    assert outbox.last_invoice_no == 3


def test_numbering_can_continue_from_another_system(tmp_path: Path, client: OscuClient) -> None:
    with SaleOutbox(tmp_path / "x.sqlite3", client, start_after=41) as box:
        assert box.enqueue(make_sale(invoice_no=None)) == 42
    with SaleOutbox(tmp_path / "x.sqlite3", client, start_after=0) as reopened:
        assert reopened.enqueue(make_sale(invoice_no=None)) == 43  # start_after only applies once


def test_invalid_sales_do_not_use_up_a_number(outbox: SaleOutbox) -> None:
    with pytest.raises(InvalidDataError):
        outbox.enqueue(make_sale(invoice_no=None, customer_pin="bad"))
    assert outbox.last_invoice_no == 0
    assert outbox.enqueue(make_sale(invoice_no=None)) == 1


def test_sales_must_not_bring_their_own_number(outbox: SaleOutbox) -> None:
    with pytest.raises(InvalidDataError, match="allocates"):
        outbox.enqueue(make_sale(invoice_no=5))


def test_flush_sends_in_order_and_stores_receipts(
    api: responses.RequestsMock, outbox: SaleOutbox
) -> None:
    for _ in range(2):
        outbox.enqueue(make_sale(invoice_no=None))
    api.post(SALES, json=envelope(receipt(1)))
    api.post(SALES, json=envelope(receipt(2)))

    report = outbox.flush()

    assert report.sent == [1, 2]
    assert report.stopped_reason is None
    assert [sent_body(c)["invcNo"] for c in api.calls] == [1, 2]
    entry = outbox.get(2)
    assert entry is not None
    assert entry.status is OutboxStatus.SENT
    assert entry.receipt is not None
    assert entry.receipt.signature == "SIGN000000000002"
    assert entry.attempts == 1
    assert outbox.flush().sent == []  # nothing left


def test_kra_outage_keeps_sales_pending(api: responses.RequestsMock, outbox: SaleOutbox) -> None:
    outbox.enqueue(make_sale(invoice_no=None))
    outbox.enqueue(make_sale(invoice_no=None))
    api.post(SALES, body=connection_refused())

    report = outbox.flush()

    assert report.sent == []
    assert report.stopped_reason is not None
    assert "unreachable" in report.stopped_reason
    assert [e.status for e in outbox.entries()] == [OutboxStatus.PENDING, OutboxStatus.PENDING]

    api.replace(responses.POST, SALES, json=envelope(receipt(1)))
    assert outbox.flush().sent == [1, 2]


def test_lost_answer_is_resent_and_confirmed_by_duplicate(
    api: responses.RequestsMock, outbox: SaleOutbox
) -> None:
    outbox.enqueue(make_sale(invoice_no=None))
    outbox.enqueue(make_sale(invoice_no=None))
    api.post(SALES, body=requests.ReadTimeout("read timed out"))

    first = outbox.flush()
    assert first.uncertain == [1]
    assert outbox.get(2) is not None
    assert outbox.get(2).status is OutboxStatus.PENDING

    api.replace(responses.POST, SALES, json=envelope(None, code="921", message="already declared"))
    api.add(responses.POST, SALES, json=envelope(receipt(2)))
    second = outbox.flush()

    assert second.duplicates == [1]
    assert second.sent == [2]
    resent, original = sent_body(api.calls[1]), sent_body(api.calls[0])
    assert resent == original  # exactly the same request, so KRA can recognise it


def test_rejection_stops_the_queue_until_fixed(
    api: responses.RequestsMock, outbox: SaleOutbox
) -> None:
    outbox.enqueue(make_sale(invoice_no=None, trader_invoice_no="INV-1"))
    outbox.enqueue(make_sale(invoice_no=None, trader_invoice_no="INV-2"))
    api.post(SALES, json=envelope(None, code="910", message="Request parameter error"))

    report = outbox.flush()
    assert report.rejected == [1]
    assert len(api.calls) == 1  # invoice 2 waits behind invoice 1
    rejected = outbox.get(1)
    assert rejected is not None
    assert rejected.last_error == "KRA 910: Request parameter error"

    outbox.requeue(1, make_sale(invoice_no=None, trader_invoice_no="INV-1-fixed"))
    fixed = outbox.get(1)
    assert fixed is not None
    assert fixed.status is OutboxStatus.PENDING
    assert fixed.trader_invoice_no == "INV-1-fixed"

    api.replace(responses.POST, SALES, json=envelope(receipt(1)))
    assert outbox.flush().sent == [1, 2]
    assert sent_body(api.calls[1])["trdInvcNo"] == "INV-1-fixed"
    assert sent_body(api.calls[1])["invcNo"] == 1


def test_duplicate_on_first_attempt_is_a_rejection(
    api: responses.RequestsMock, outbox: SaleOutbox
) -> None:
    outbox.enqueue(make_sale(invoice_no=None))
    api.post(SALES, json=envelope(None, code="994", message="There is an overlapped Data"))
    report = outbox.flush()
    assert report.rejected == [1]
    assert report.duplicates == []


def test_kra_server_failure_is_treated_as_uncertain(
    api: responses.RequestsMock, outbox: SaleOutbox
) -> None:
    outbox.enqueue(make_sale(invoice_no=None))
    api.post(SALES, json=envelope(None, code="999", message="unknown error"))
    report = outbox.flush()
    assert report.uncertain == [1]
    entry = outbox.get(1)
    assert entry is not None
    assert entry.status is OutboxStatus.UNCERTAIN


def test_flush_limit(api: responses.RequestsMock, outbox: SaleOutbox) -> None:
    for _ in range(3):
        outbox.enqueue(make_sale(invoice_no=None))
    api.post(SALES, json=envelope(receipt(1)))
    assert outbox.flush(limit=2).sent == [1, 2]
    assert [e.invoice_no for e in outbox.entries(OutboxStatus.PENDING)] == [3]


def test_requeue_rules(outbox: SaleOutbox) -> None:
    with pytest.raises(InvalidDataError, match="not in the outbox"):
        outbox.requeue(9, make_sale(invoice_no=None))
    outbox.enqueue(make_sale(invoice_no=None))
    with pytest.raises(InvalidDataError, match="Only rejected"):
        outbox.requeue(1, make_sale(invoice_no=None))


def test_outbox_survives_restarts(tmp_path: Path, client: OscuClient) -> None:
    path = tmp_path / "etims.sqlite3"
    with SaleOutbox(path, client) as box:
        box.enqueue(make_sale(invoice_no=None))
    with SaleOutbox(path, client) as box:
        assert box.last_invoice_no == 1
        assert [e.status for e in box.entries()] == [OutboxStatus.PENDING]
        assert box.get(99) is None


def test_each_branch_has_its_own_numbering(tmp_path: Path) -> None:
    path = tmp_path / "shared.sqlite3"
    head = OscuClient("P051234567A", "00", "key")
    branch = OscuClient("P051234567A", "01", "key")
    with SaleOutbox(path, head) as a, SaleOutbox(path, branch) as b:
        assert a.enqueue(make_sale(invoice_no=None)) == 1
        assert b.enqueue(make_sale(invoice_no=None)) == 1
        assert a.enqueue(make_sale(invoice_no=None)) == 2
        assert len(a.entries()) == 2
        assert len(b.entries()) == 1


def test_enqueue_is_not_blocked_while_flush_waits_for_kra(
    api: responses.RequestsMock, outbox: SaleOutbox
) -> None:
    outbox.enqueue(make_sale(invoice_no=None))
    in_flight, release = threading.Event(), threading.Event()

    def slow_kra(request: object) -> tuple[int, dict[str, str], str]:
        in_flight.set()
        release.wait(5)
        return 200, {}, json.dumps(envelope(receipt(1)))

    api.add_callback(responses.POST, SALES, callback=slow_kra)
    flusher = threading.Thread(target=outbox.flush)
    flusher.start()
    try:
        assert in_flight.wait(5)
        numbers: list[int] = []
        writer = threading.Thread(
            target=lambda: numbers.append(outbox.enqueue(make_sale(invoice_no=None)))
        )
        writer.start()
        writer.join(timeout=2)
        assert numbers == [2], "enqueue() waited for KRA's answer"
    finally:
        release.set()
        flusher.join(5)
    entry = outbox.get(1)
    assert entry is not None
    assert entry.status is OutboxStatus.SENT
