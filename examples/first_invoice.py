"""Register an item, then sign an invoice and a credit note in the KRA SANDBOX.

    uv run python examples/first_invoice.py

This creates test data in the sandbox, and uses the outbox in
``sandbox-outbox.sqlite3`` so invoice numbers stay in sequence between runs.
Needs OSCU_TIN, OSCU_BHF_ID and OSCU_CMC_KEY in the environment or .env.
"""

from __future__ import annotations

import sys
from datetime import datetime
from decimal import Decimal

from _env import load_settings

from etims import (
    EAT,
    CreditNoteReason,
    Environment,
    Item,
    OscuClient,
    OutboxStatus,
    PaymentType,
    ProductType,
    ReceiptType,
    Sale,
    SaleLine,
    SaleOutbox,
    TaxType,
    make_item_code,
)

ITEM_CLASS = "8111180000"  # pick a real one from client.get_item_classes()


def main() -> int:
    client = OscuClient.from_env(environ=load_settings())
    if client.environment is not Environment.SANDBOX:
        print("This example only runs against the sandbox.")
        return 1

    item = Item(
        item_code=make_item_code(ProductType.SERVICE, "NT", "U", 1),
        item_class_code=ITEM_CLASS,
        product_type=ProductType.SERVICE,
        name="Systems integration (hour)",
        packaging_unit="NT",
        quantity_unit="U",
        tax_type=TaxType.STANDARD,
        default_price=Decimal("5800.00"),
    )
    client.save_item(item)
    print(f"Registered item {item.item_code}")

    line = SaleLine(
        item_code=item.item_code,
        item_class_code=item.item_class_code,
        name=item.name,
        quantity=3,
        unit_price=Decimal("5800.00"),  # VAT included
        tax_type=TaxType.STANDARD,
    )
    now = datetime.now(EAT)
    with SaleOutbox("sandbox-outbox.sqlite3", client) as outbox:
        invoice_no = outbox.enqueue(
            Sale(
                trader_invoice_no=f"DEMO-{now:%Y%m%d%H%M%S}",
                lines=[line],
                confirmed_at=now,
                payment_type=PaymentType.MOBILE_MONEY,
            )
        )
        outbox.enqueue(
            Sale(
                trader_invoice_no=f"DEMO-{now:%Y%m%d%H%M%S}-CN",
                lines=[line],
                confirmed_at=now,
                receipt_type=ReceiptType.CREDIT_NOTE,
                original_invoice_no=invoice_no,
                credit_note_reason=CreditNoteReason.REFUND,
                payment_type=PaymentType.MOBILE_MONEY,
            )
        )
        report = outbox.flush()
        print(f"Sent: {report.sent}  Problems: {report.stopped_reason or 'none'}")
        for entry in outbox.entries():
            if entry.status is OutboxStatus.SENT and entry.receipt:
                print(f"  Invoice {entry.invoice_no}: signature {entry.receipt.signature}")
                print(f"    QR: {entry.receipt.qr_url}")
            elif entry.status is not OutboxStatus.SENT:
                problem = entry.last_error or ""
                print(f"  Invoice {entry.invoice_no}: {entry.status.value} {problem}")
    return 0 if not report.stopped_reason else 2


if __name__ == "__main__":
    sys.exit(main())
