"""Sales invoices, credit notes, and the signed receipts KRA returns."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from ._payload import Body, Operator, Record
from ._util import Number, is_valid_pin, to_decimal
from .codes import (
    CreditNoteReason,
    PaymentType,
    ReceiptType,
    SalesType,
    TaxType,
    TransactionStatus,
)
from .environment import Environment
from .errors import InvalidDataError
from .tax import LineAmounts, Totals, compute_line, summarize


@dataclass(frozen=True, kw_only=True)
class SaleLine:
    """One line of a sales invoice.

    ``unit_price`` includes VAT, as eTIMS requires. If your system keeps
    prices without VAT, convert with ``etims.tax.gross_price()``, or pass the
    VAT-inclusive ``amounts`` your system already calculated so KRA receives
    exactly the figures on your invoice.
    """

    item_code: str
    item_class_code: str
    name: str
    quantity: Number
    unit_price: Number
    tax_type: TaxType
    packaging_unit: str = "NT"
    quantity_unit: str = "U"
    #: Number of packages (``pkg``). Defaults to ``quantity``.
    package_quantity: Number | None = None
    discount_rate: Number = 0
    barcode: str | None = None
    insurance_code: str | None = None
    insurance_name: str | None = None
    insurance_rate: int | None = None
    insurance_amount: Number | None = None
    #: Amounts computed by your own system; sent as-is when given.
    amounts: LineAmounts | None = None

    def compute(self, rates: Mapping[TaxType, Decimal] | None = None) -> LineAmounts:
        if self.amounts is not None:
            return self.amounts
        return compute_line(
            self.quantity,
            self.unit_price,
            self.tax_type,
            discount_rate=self.discount_rate,
            rates=rates,
        )

    def to_api(self, seq: int, amounts: LineAmounts) -> dict[str, Any]:
        if to_decimal(self.quantity) <= 0:
            raise InvalidDataError(f"SaleLine[{seq}].qty must be greater than zero")
        if to_decimal(self.unit_price) < 0:
            raise InvalidDataError(f"SaleLine[{seq}].prc must not be negative")
        package = self.quantity if self.package_quantity is None else self.package_quantity
        return (
            Body(f"SaleLine[{seq}]")
            .integer("itemSeq", seq, required=True, minimum=1, maximum=999)
            .text("itemClsCd", self.item_class_code, 10, required=True)
            .text("itemCd", self.item_code, 20, required=True)
            .text("itemNm", self.name, 200, required=True)
            .text("bcd", self.barcode, 20)
            # qty before pkg: pkg defaults to qty, so errors should name qty.
            .text("qtyUnitCd", self.quantity_unit, 5, required=True)
            .amount("qty", self.quantity, required=True)
            .text("pkgUnitCd", self.packaging_unit, 5, required=True)
            .amount("pkg", package, required=True)
            .amount("prc", self.unit_price, required=True)
            .amount("splyAmt", amounts.supply_amount, required=True)
            .amount("dcRt", self.discount_rate, required=True)
            .amount("dcAmt", amounts.discount_amount, required=True)
            .text("isrccCd", self.insurance_code, 10)
            .text("isrccNm", self.insurance_name, 100)
            .integer("isrcRt", self.insurance_rate, minimum=0, maximum=100)
            .amount("isrcAmt", self.insurance_amount)
            .code("taxTyCd", self.tax_type, required=True)
            .amount("taxblAmt", amounts.taxable_amount, required=True)
            # The spec's table names this "totTaxAmt"; its sample and every other
            # line object in the API use "taxAmt".
            .amount("taxAmt", amounts.tax_amount, required=True)
            .amount("totAmt", amounts.total_amount, required=True)
            .build()
        )


@dataclass(frozen=True, kw_only=True)
class ReceiptDetails:
    """Optional extras printed on the receipt (note KRA's short length limits)."""

    #: When the receipt was issued (``rcptPbctDt``). Defaults to the sale's confirmation time.
    published_at: datetime | None = None
    trade_name: str | None = None  # at most 20 characters
    address: str | None = None  # at most 200
    top_message: str | None = None  # at most 20
    bottom_message: str | None = None  # at most 20


@dataclass(frozen=True, kw_only=True)
class Sale:
    """A sales invoice or credit note to sign with KRA (``save_sale``).

    ``invoice_no`` is KRA's invoice number for your branch: it must start at
    1 and increase by exactly one per invoice, with no gaps, so allocate it
    only when you are about to send. ``SaleOutbox`` does this for you.
    ``trader_invoice_no`` is your own reference (e.g. "INV/2026/0042").

    For a credit note, set ``receipt_type=ReceiptType.CREDIT_NOTE``,
    ``original_invoice_no`` (the KRA invoice number being credited) and
    ``credit_note_reason``. A credit note uses a new ``invoice_no`` of its own.
    """

    trader_invoice_no: str
    lines: Sequence[SaleLine]
    confirmed_at: datetime
    invoice_no: int | None = None
    receipt_type: ReceiptType = ReceiptType.SALE
    original_invoice_no: int | None = None
    credit_note_reason: CreditNoteReason | None = None
    #: Credit note date (``rfdDt``). Defaults to ``confirmed_at`` for credit notes.
    credit_note_at: datetime | None = None
    customer_pin: str | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    payment_type: PaymentType | None = PaymentType.CASH
    status: TransactionStatus = TransactionStatus.APPROVED
    sales_type: SalesType = SalesType.NORMAL
    #: Defaults to the date of ``confirmed_at`` (in Kenyan time).
    sale_date: date | None = None
    stock_released_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    cancelled_at: datetime | None = None
    #: Whether the buyer accepted the purchase (``prchrAcptcYn``).
    purchase_accepted: bool = False
    remark: str | None = None
    receipt: ReceiptDetails = field(default_factory=ReceiptDetails)

    @property
    def is_credit_note(self) -> bool:
        return self.receipt_type is ReceiptType.CREDIT_NOTE

    def _check(self) -> None:
        if self.invoice_no is None:
            raise InvalidDataError(
                "Sale.invoice_no is required (or send the sale through a SaleOutbox, "
                "which allocates invoice numbers)"
            )
        if self.invoice_no < 1:
            raise InvalidDataError("Sale.invoice_no must be 1 or more")
        if not self.lines:
            raise InvalidDataError("Sale.lines must contain at least one line")
        if len(self.lines) > 999:
            raise InvalidDataError("Sale.lines allows at most 999 lines")
        if self.customer_pin and not is_valid_pin(self.customer_pin):
            raise InvalidDataError(
                f"Sale.custTin {self.customer_pin!r} is not a valid KRA PIN (e.g. P051234567A)"
            )
        if self.is_credit_note:
            if not self.original_invoice_no or self.original_invoice_no < 1:
                raise InvalidDataError(
                    "A credit note needs original_invoice_no (the KRA invoice number it credits)"
                )
            if self.original_invoice_no == self.invoice_no:
                raise InvalidDataError("A credit note needs its own invoice_no")
            if self.credit_note_reason is None:
                raise InvalidDataError("A credit note needs a credit_note_reason")
        elif self.original_invoice_no or self.credit_note_reason or self.credit_note_at:
            raise InvalidDataError(
                "original_invoice_no, credit_note_reason and credit_note_at are only for "
                "credit notes (receipt_type=ReceiptType.CREDIT_NOTE)"
            )

    def totals(self, rates: Mapping[TaxType, Decimal] | None = None) -> Totals:
        return summarize(((line.tax_type, line.compute(rates)) for line in self.lines), rates)

    def to_api(
        self, operator: Operator, rates: Mapping[TaxType, Decimal] | None = None
    ) -> dict[str, Any]:
        """The request body for /saveTrnsSalesOsdc, without tin/bhfId/cmcKey."""
        self._check()
        computed = [line.compute(rates) for line in self.lines]
        totals = summarize(
            zip((line.tax_type for line in self.lines), computed, strict=True), rates
        )
        credit_note_at = (self.credit_note_at or self.confirmed_at) if self.is_credit_note else None
        body = (
            Body("Sale")
            .text("trdInvcNo", self.trader_invoice_no, 50, required=True)
            .integer("invcNo", self.invoice_no, required=True, minimum=1)
            .integer("orgInvcNo", self.original_invoice_no or 0, required=True, minimum=0)
            .text("custTin", self.customer_pin, 11)
            .text("custNm", self.customer_name, 60)
            .code("salesTyCd", self.sales_type, required=True)
            .code("rcptTyCd", self.receipt_type, required=True)
            .code("pmtTyCd", self.payment_type)
            .code("salesSttsCd", self.status, required=True)
            .timestamp("cfmDt", self.confirmed_at, required=True)
            .day("salesDt", self.sale_date or self.confirmed_at, required=True)
            .timestamp("stockRlsDt", self.stock_released_at)
            .timestamp("cnclReqDt", self.cancel_requested_at)
            .timestamp("cnclDt", self.cancelled_at)
            .timestamp("rfdDt", credit_note_at)
            .code("rfdRsnCd", self.credit_note_reason)
            .integer("totItemCnt", len(self.lines), required=True)
        )
        for key, value in totals.to_api().items():
            body.set(key, value)
        body.flag("prchrAcptcYn", self.purchase_accepted).text("remark", self.remark, 400)
        body.operator(operator)
        receipt = (
            Body("Sale.receipt")
            .text("custTin", self.customer_pin, 11)
            .text("custMblNo", self.customer_phone, 20)
            .timestamp("rcptPbctDt", self.receipt.published_at or self.confirmed_at, required=True)
            .text("trdeNm", self.receipt.trade_name, 20)
            .text("adrs", self.receipt.address, 200)
            .text("topMsg", self.receipt.top_message, 20)
            .text("btmMsg", self.receipt.bottom_message, 20)
            .flag("prchrAcptcYn", self.purchase_accepted)
            .build()
        )
        body.set("receipt", receipt)
        body.set(
            "itemList",
            [
                line.to_api(seq, amounts)
                for seq, (line, amounts) in enumerate(
                    zip(self.lines, computed, strict=True), start=1
                )
            ],
        )
        return body.build()


@dataclass(frozen=True, kw_only=True)
class SaleReceipt:
    """KRA's signature for a sale. Store all of it with the invoice and print it."""

    invoice_no: int
    #: Receipt number for this receipt type (``curRcptNo``).
    receipt_no: int | None
    #: Running number across all receipts (``totRcptNo``).
    total_receipt_no: int | None
    internal_data: str | None
    signature: str | None
    #: When KRA's control unit signed the sale (``sdcDateTime``).
    signed_at: datetime | None
    tin: str
    branch_id: str
    environment: Environment
    raw: Mapping[str, Any] = field(repr=False)

    @property
    def qr_data(self) -> str | None:
        """PIN + branch + receipt signature, the data KRA's receipt page looks up."""
        if not self.signature:
            return None
        return f"{self.tin}{self.branch_id}{self.signature}"

    @property
    def qr_url(self) -> str | None:
        """The link to encode in the invoice's QR code."""
        data = self.qr_data
        if data is None:
            return None
        return f"{self.environment.receipt_url}?Data={quote(data, safe='')}"

    @classmethod
    def from_api(
        cls,
        data: Mapping[str, Any],
        *,
        invoice_no: int,
        tin: str,
        branch_id: str,
        environment: Environment,
    ) -> SaleReceipt:
        r = Record(data)
        return cls(
            invoice_no=invoice_no,
            receipt_no=r.integer("curRcptNo"),
            total_receipt_no=r.integer("totRcptNo"),
            internal_data=r.text("intrlData"),
            signature=r.text("rcptSign"),
            signed_at=r.timestamp("sdcDateTime"),
            tin=tin,
            branch_id=branch_id,
            environment=environment,
            raw=r.raw,
        )
