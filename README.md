# etims

[![CI](https://github.com/MutaiT/etims/actions/workflows/ci.yml/badge.svg)](https://github.com/MutaiT/etims/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/etims)](https://pypi.org/project/etims/)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue)](https://pypi.org/project/etims/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

An open-source Python client for **KRA eTIMS**, the Kenya Revenue Authority's
electronic tax invoice system. It talks to KRA's **OSCU** (Online Sales Control
Unit) API directly: no middleware account, no Java library.

- Sign sales invoices and credit notes; get the receipt signature and QR link
- Register items, confirm purchases, report stock, sync KRA's code lists
- Typed models that check KRA's field rules *before* sending, so a mistake fails
  with a clear message instead of a vague `910 Request parameter error`
- VAT calculated the way eTIMS counts it (VAT-inclusive, per line, in `Decimal`)
- Safe retries and an offline outbox: gap-free invoice numbers, in-order
  delivery through KRA outages, and no invoice signed twice
- One dependency (`requests`), Python 3.10+, so it drops into Odoo, Django or
  a FastAPI service

> **Status: alpha.** Built from KRA's
> [OSCU specification v2.0](https://www.kra.go.ke/images/publications/OSCU_Specification_Document_v2.0.pdf)
> and tested against a simulated API. Signing against the live sandbox is still
> to be verified; see [What's verified](#whats-verified).

## Install

```bash
pip install etims
```

Current release is an alpha (`0.1.0a1`). To track the latest code instead:
`pip install git+https://github.com/MutaiT/etims`.

## Quick start

### 1. Get your communication key (once per branch)

KRA approves an OSCU device serial number for your PIN and branch. Verify it
once to receive the communication key every other call needs:

```python
from etims import OscuClient

device = OscuClient.initialize_device("P051234567A", "00", "your-device-serial")
print(device.taxpayer_name, device.branch_name)
cmc_key = device.cmc_key  # store it as a secret, e.g. OSCU_CMC_KEY
```

### 2. Register what you sell

```python
from decimal import Decimal
from etims import Item, ProductType, TaxType, make_item_code

client = OscuClient("P051234567A", "00", cmc_key)  # sandbox by default

client.save_item(
    Item(
        item_code=make_item_code(ProductType.SERVICE, "NT", "U", 1),  # "KE3NTU0000001"
        item_class_code="8111180000",  # from client.get_item_classes()
        product_type=ProductType.SERVICE,
        name="Systems integration (hour)",
        packaging_unit="NT",
        quantity_unit="U",
        tax_type=TaxType.STANDARD,  # B, 16%
        default_price=Decimal("5800.00"),
    )
)
```

### 3. Sign an invoice

```python
from datetime import datetime
from etims import EAT, PaymentType, Sale, SaleLine

receipt = client.save_sale(
    Sale(
        invoice_no=1,  # KRA's sequence for this branch: 1, 2, 3...
        trader_invoice_no="INV/2026/0042",
        confirmed_at=datetime.now(EAT),
        customer_pin="A123456789Z",  # optional
        payment_type=PaymentType.MOBILE_MONEY,
        lines=[
            SaleLine(
                item_code="KE3NTU0000001",
                item_class_code="8111180000",
                name="Systems integration (hour)",
                quantity=3,
                unit_price=Decimal("5800.00"),  # VAT included
                tax_type=TaxType.STANDARD,
            )
        ],
    )
)

receipt.signature  # print these on the invoice
receipt.qr_url  # encode this in the invoice's QR code
```

### 4. Credit notes

A signed invoice can't be cancelled, only credited. A credit note is a sale with
its own invoice number that points at the original:

```python
from etims import CreditNoteReason, ReceiptType

client.save_sale(
    Sale(
        invoice_no=2,
        original_invoice_no=1,
        receipt_type=ReceiptType.CREDIT_NOTE,
        credit_note_reason=CreditNoteReason.REFUND,
        trader_invoice_no="CN/2026/0007",
        confirmed_at=datetime.now(EAT),
        lines=[...],
    )
)
```

## Sending reliably: the outbox

eTIMS invoice numbers must increase by one with no gaps, KRA is sometimes
unreachable, and a sale whose answer was lost must not be signed twice.
`SaleOutbox` handles all three using SQLite from the standard library:

```python
from etims import SaleOutbox

with SaleOutbox("etims.sqlite3", client) as outbox:
    invoice_no = outbox.enqueue(sale)  # leave sale.invoice_no unset; the outbox numbers it
    report = outbox.flush()  # e.g. from a job that runs every minute

report.sent  # [41, 42]
report.stopped_reason  # None, or why it paused (KRA down, invoice rejected...)
```

- A sale that fails validation never uses up a number.
- Sales go out in number order. If KRA is down, they stay queued and the next
  `flush()` resumes.
- If KRA doesn't answer, the sale is marked *uncertain* and re-sent unchanged.
  A `DuplicateError` on the resend proves KRA already had it.
- A rejected sale stops the queue until you fix it with `requeue()`, which keeps
  its invoice number.

## Testing without KRA

`etims.testing.FakeKra` is a local stand-in for the OSCU API. It initializes
devices, registers items and signs sales, and it is stricter than the
specification where that catches mistakes early: invoice numbering, item
registration and totals. Use it in tests:

```python
from etims.testing import FakeKra

with FakeKra() as kra:
    device = OscuClient.initialize_device("P051234567A", "00", "any-serial", base_url=kra.url)
    client = OscuClient("P051234567A", "00", device.cmc_key, base_url=kra.url)
    kra.fail_next("/saveTrnsSalesOsdc", drop=True)  # simulate a dropped connection
```

or run it as a server for demos and ERP integrations:

```bash
python -m etims.testing --port 8088 --state-file kra.json
```

Passing against the fake doesn't prove KRA accepts a request; it's for
development and CI.

## Errors and retries

Every error derives from `etims.EtimsError`, and every KRA result code maps to a
specific exception:

| Exception | When | Retried automatically? |
|---|---|---|
| `InvalidDataError` | Data breaks a KRA rule; nothing was sent | No: fix the data |
| `ConnectionFailedError` | Couldn't connect, so nothing reached KRA | Yes, lookups and saves |
| `ResponseTimeoutError` | Sent, but no answer; KRA *may* have it | Lookups only |
| `ProtocolError` | HTTP error page, invalid JSON... | Lookups only (5xx/429) |
| `DeviceError` | 901, 902, 903, 905: device not set up for OSCU | No |
| `RequestError` | 910-912: KRA rejected the request content | No |
| `DuplicateError` | 921, 994: KRA already has this record | No |
| `KraServerError` | 990-999: KRA failed internally | Lookups only |

Saves are never re-sent automatically once they may have reached KRA. Errors
that leave that open carry `may_have_been_processed=True`. Resend the *same*
sale and let a `DuplicateError` confirm it, or use the outbox, which does this
for you.

## VAT: how eTIMS counts it

eTIMS amounts include VAT. KRA's specification shows a taxable amount of
10,500 at 18% carrying 1,602 of tax (10,500 × 18/118), with the total equal to
the taxable amount. At Kenya's 16%:

```
taxable = quantity × unit price − discount     (VAT included)
tax     = taxable × 16 / 116                   (the VAT inside the price)
```

Lines are rounded to the cent (half up) and the invoice totals are the sums of
the rounded lines, so the header always agrees with the lines. If your system
stores prices without VAT, convert them with `gross_price()`, or pass the exact
figures your system computed as `SaleLine(amounts=LineAmounts(...))`.

Rates default to the specification's (A exempt, B 16%, C 0%, D non-VAT, E 8%).
To follow KRA's published rates instead:

```python
from etims import tax_rates_from_codes

client = OscuClient(tin, bhf_id, cmc_key, tax_rates=tax_rates_from_codes(client.get_codes()))
```

## Keeping in sync with KRA

Lookups return what changed after a date. Each result carries KRA's server
time, `as_of`; store it and pass it as `since` next time:

```python
classes = client.get_item_classes()  # everything
later = client.get_item_classes(since=classes.as_of)  # only changes
```

## API coverage

| Method | KRA endpoint |
|---|---|
| `OscuClient.initialize_device()` | `/selectInitOsdcInfo` |
| `get_codes()` | `/selectCodeList` |
| `get_item_classes()` | `/selectItemClsList` |
| `get_taxpayer()` | `/selectCustomer` |
| `get_branches()` | `/selectBhfList` |
| `get_notices()` | `/selectNoticeList` |
| `save_branch_customer()`, `save_branch_user()`, `save_branch_insurance()` | `/saveBhfCustomer`, `/saveBhfUser`, `/saveBhfInsurance` |
| `save_item()`, `get_items()` | `/saveItem`, `/selectItemList` |
| `get_imported_items()`, `update_imported_item()` | `/selectImportItemList`, `/updateImportItem` |
| `save_sale()` | `/saveTrnsSalesOsdc` |
| `get_purchase_sales()`, `save_purchase()` | `/selectTrnsPurchaseSalesList`, `/insertTrnsPurchase` |
| `get_stock_movements()`, `save_stock_io()`, `save_stock_master()` | `/selectStockMoveList`, `/insertStockIO`, `/saveStockMaster` |

Not yet covered: item composition (`saveItemComposition`), which the
specification lists but doesn't define.

## Notes on the specification

Places where KRA's specification disagrees with itself, and what this library
does about them:

- **Credentials.** The request tables list `tin`, `bhfId` and `cmcKey` as fields,
  but the samples omit `cmcKey`, and code 900 is "There is no Header
  information". The client sends them as headers *and* in the body.
- **Result codes.** The live sandbox returns **905** "Only OSCU device can be
  verified", which the specification lists as 903. Both map to `DeviceError`.
- **Field names.** `sdcId` (sample) vs `sdicId` (table) in device info; `taxAmt`
  (sample) vs `totTaxAmt` (table) on sales lines; `imptItemSttsCd` vs
  `imptItemsttsCd` for imports. The client sends the sample spelling and reads
  both.
- **Sales type.** `salesTyCd` appears in the sample but not the table; it is sent.
- **VAT.** The sales, stock and stock-movement samples are VAT-inclusive; the
  purchase sample alone adds VAT on top. The client is consistently inclusive.
- **Short receipt fields.** Trade name, top message and bottom message are
  limited to 20 characters, and the client enforces that.
- **QR link.** The receipt URL format isn't in the specification; the client
  uses the format KRA's receipt pages use.

## What's verified

- **Against the specification:** every endpoint, field name, length limit and
  code list, plus end-to-end runs over HTTP against the bundled fake KRA
  (176 tests, run on Python 3.10 to 3.14).
- **Against the live sandbox:** device initialization and its error handling.
  Signing invoices live is pending an approved OSCU device; run
  `uv run pytest -m live` and `examples/first_invoice.py` to check your own setup.

## Development

```bash
uv sync                    # install with dev tools
uv run pytest              # tests (no network)
uv run pytest -m live      # read-only checks against the sandbox (needs .env)
uv run ruff check && uv run ruff format --check
uv run mypy                # strict type checking
```

Copy `.env.example` to `.env` for the live tests and `examples/`.

## License

MIT. This project is not affiliated with or endorsed by the Kenya Revenue
Authority. Production use of OSCU requires KRA's approval of your integration.
