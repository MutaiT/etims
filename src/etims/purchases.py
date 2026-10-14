"""Purchases: sales your suppliers declared to KRA, and your confirmation of them."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ._payload import Body, Operator, Record
from ._util import Number, to_decimal
from .codes import (
    PaymentType,
    PurchaseReceiptType,
    RegistrationType,
    SalesType,
    TaxType,
    TransactionStatus,
)
from .errors import InvalidDataError
from .tax import LineAmounts, compute_line, summarize


@dataclass(frozen=True, kw_only=True)
class PurchaseSaleLine:
    item_seq: int | None
    item_class_code: str | None
    item_code: str | None
    name: str | None
    barcode: str | None
    packaging_unit: str | None
    package_quantity: Decimal | None
    quantity_unit: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    supply_amount: Decimal | None
    discount_rate: Decimal | None
    discount_amount: Decimal | None
    tax_type: TaxType | None
    taxable_amount: Decimal | None
    tax_amount: Decimal | None
    total_amount: Decimal | None

    @classmethod
    def from_api(cls, r: Record) -> PurchaseSaleLine:
        return cls(
            item_seq=r.integer("itemSeq"),
            item_class_code=r.text("itemClsCd"),
            item_code=r.text("itemCd"),
            name=r.text("itemNm"),
            barcode=r.text("bcd"),
            packaging_unit=r.text("pkgUnitCd"),
            package_quantity=r.decimal("pkg"),
            quantity_unit=r.text("qtyUnitCd"),
            quantity=r.decimal("qty"),
            unit_price=r.decimal("prc"),
            supply_amount=r.decimal("splyAmt"),
            discount_rate=r.decimal("dcRt"),
            discount_amount=r.decimal("dcAmt"),
            tax_type=r.enum(TaxType, "taxTyCd"),
            taxable_amount=r.decimal("taxblAmt"),
            tax_amount=r.decimal("taxAmt"),
            total_amount=r.decimal("totAmt"),
        )


@dataclass(frozen=True, kw_only=True)
class PurchaseSale:
    """A sale a supplier declared with your PIN as the buyer (``get_purchase_sales``)."""

    supplier_pin: str | None
    supplier_name: str | None
    supplier_branch_id: str | None
    supplier_invoice_no: int | None
    receipt_type: str | None
    payment_type: PaymentType | None
    confirmed_at: datetime | None
    sale_date: date | None
    stock_released_at: datetime | None
    total_item_count: int | None
    taxable_by_type: Mapping[TaxType, Decimal | None]
    tax_by_type: Mapping[TaxType, Decimal | None]
    rates: Mapping[TaxType, Decimal | None]
    total_taxable: Decimal | None
    total_tax: Decimal | None
    total_amount: Decimal | None
    remark: str | None
    lines: list[PurchaseSaleLine]

    @classmethod
    def from_api(cls, r: Record) -> PurchaseSale:
        return cls(
            supplier_pin=r.text("spplrTin"),
            supplier_name=r.text("spplrNm"),
            supplier_branch_id=r.text("spplrBhfId"),
            supplier_invoice_no=r.integer("spplrInvcNo"),
            receipt_type=r.text("rcptTyCd"),
            payment_type=r.enum(PaymentType, "pmtTyCd"),
            confirmed_at=r.timestamp("cfmDt"),
            sale_date=r.day("salesDt"),
            stock_released_at=r.timestamp("stockRlsDt"),
            total_item_count=r.integer("totItemCnt"),
            taxable_by_type={t: r.decimal(f"taxblAmt{t.value}") for t in TaxType},
            tax_by_type={t: r.decimal(f"taxAmt{t.value}") for t in TaxType},
            rates={t: r.decimal(f"taxRt{t.value}") for t in TaxType},
            total_taxable=r.decimal("totTaxblAmt"),
            total_tax=r.decimal("totTaxAmt"),
            total_amount=r.decimal("totAmt"),
            remark=r.text("remark"),
            lines=[PurchaseSaleLine.from_api(line) for line in r.records("itemList")],
        )


@dataclass(frozen=True, kw_only=True)
class PurchaseLine:
    """One line of a purchase you confirm. ``unit_price`` includes VAT."""

    item_class_code: str
    name: str
    quantity: Number
    unit_price: Number
    tax_type: TaxType
    quantity_unit: str = "U"
    packaging_unit: str | None = "NT"
    package_quantity: Number | None = None
    discount_rate: Number = 0
    #: Your own item code, if this purchase maps to an item you registered.
    item_code: str | None = None
    barcode: str | None = None
    supplier_item_class_code: str | None = None
    supplier_item_code: str | None = None
    supplier_item_name: str | None = None
    expiry_date: date | None = None
    #: Amounts to send as-is instead of computing them (e.g. the supplier's figures).
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
            raise InvalidDataError(f"PurchaseLine[{seq}].qty must be greater than zero")
        package = self.quantity if self.package_quantity is None else self.package_quantity
        return (
            Body(f"PurchaseLine[{seq}]")
            .integer("itemSeq", seq, required=True, minimum=1, maximum=999)
            .text("itemCd", self.item_code, 20)
            .text("itemClsCd", self.item_class_code, 10, required=True)
            .text("itemNm", self.name, 200, required=True)
            .text("bcd", self.barcode, 20)
            .text("spplrItemClsCd", self.supplier_item_class_code, 10)
            .text("spplrItemCd", self.supplier_item_code, 20)
            .text("spplrItemNm", self.supplier_item_name, 200)
            # qty before pkg: pkg defaults to qty, so errors should name qty.
            .text("qtyUnitCd", self.quantity_unit, 5, required=True)
            .amount("qty", self.quantity, required=True)
            .text("pkgUnitCd", self.packaging_unit, 5)
            .amount("pkg", package, required=True)
            .amount("prc", self.unit_price, required=True)
            .amount("splyAmt", amounts.supply_amount, required=True)
            .amount("dcRt", self.discount_rate, required=True)
            .amount("dcAmt", amounts.discount_amount, required=True)
            .amount("taxblAmt", amounts.taxable_amount, required=True)
            .code("taxTyCd", self.tax_type, required=True)
            .amount("taxAmt", amounts.tax_amount, required=True)
            .amount("totAmt", amounts.total_amount, required=True)
            .day("itemExprDt", self.expiry_date)
            .build()
        )


@dataclass(frozen=True, kw_only=True)
class Purchase:
    """A purchase to record with KRA (``save_purchase``).

    ``invoice_no`` is your own running purchase number for the branch.
    """

    invoice_no: int
    lines: Sequence[PurchaseLine]
    purchase_date: date
    payment_type: PaymentType
    supplier_pin: str | None = None
    supplier_branch_id: str | None = None
    supplier_name: str | None = None
    supplier_invoice_no: int | None = None
    original_invoice_no: int = 0
    registration_type: RegistrationType = RegistrationType.MANUAL
    purchase_type: SalesType = SalesType.NORMAL
    receipt_type: PurchaseReceiptType = PurchaseReceiptType.PURCHASE
    status: TransactionStatus = TransactionStatus.APPROVED
    confirmed_at: datetime | None = None
    warehoused_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    cancelled_at: datetime | None = None
    credit_note_at: datetime | None = None
    remark: str | None = None

    @classmethod
    def from_purchase_sale(
        cls,
        sale: PurchaseSale,
        *,
        invoice_no: int,
        purchase_date: date | None = None,
        map_item: Callable[[PurchaseSaleLine], tuple[str, str] | None] | None = None,
        **overrides: Any,
    ) -> Purchase:
        """Confirm a supplier's declared sale as your purchase.

        Line amounts are copied exactly from the supplier's declaration.
        ``map_item(line)`` may return ``(your_item_code, your_item_class_code)``
        to link a line to one of your registered items.
        """
        lines = []
        for line in sale.lines:
            if (
                line.tax_type is None
                or line.quantity is None
                or line.unit_price is None
                or line.taxable_amount is None
                or line.tax_amount is None
            ):
                raise InvalidDataError(
                    f"Supplier line {line.item_seq} is missing its quantity, price, "
                    "tax type or amounts"
                )
            mapped = map_item(line) if map_item else None
            item_code, class_code = mapped if mapped else (None, line.item_class_code or "")
            lines.append(
                PurchaseLine(
                    item_class_code=class_code,
                    name=line.name or "",
                    quantity=line.quantity,
                    unit_price=line.unit_price,
                    tax_type=line.tax_type,
                    quantity_unit=line.quantity_unit or "U",
                    packaging_unit=line.packaging_unit,
                    package_quantity=line.package_quantity,
                    discount_rate=0 if line.discount_rate is None else line.discount_rate,
                    item_code=item_code,
                    barcode=line.barcode,
                    supplier_item_class_code=line.item_class_code,
                    supplier_item_code=line.item_code,
                    supplier_item_name=line.name,
                    amounts=LineAmounts(
                        supply_amount=(
                            line.taxable_amount
                            if line.supply_amount is None
                            else line.supply_amount
                        ),
                        discount_amount=(
                            Decimal("0.00")
                            if line.discount_amount is None
                            else line.discount_amount
                        ),
                        taxable_amount=line.taxable_amount,
                        tax_amount=line.tax_amount,
                    ),
                )
            )
        fields: dict[str, Any] = {
            "invoice_no": invoice_no,
            "lines": lines,
            "purchase_date": purchase_date or sale.sale_date or date.today(),
            "payment_type": sale.payment_type or PaymentType.CASH,
            "supplier_pin": sale.supplier_pin,
            "supplier_branch_id": sale.supplier_branch_id,
            "supplier_name": sale.supplier_name,
            "supplier_invoice_no": sale.supplier_invoice_no,
            "registration_type": RegistrationType.AUTOMATIC,
            "confirmed_at": sale.confirmed_at,
        }
        fields.update(overrides)
        return cls(**fields)

    def to_api(
        self, operator: Operator, rates: Mapping[TaxType, Decimal] | None = None
    ) -> dict[str, Any]:
        if not self.lines:
            raise InvalidDataError("Purchase.lines must contain at least one line")
        if len(self.lines) > 999:
            raise InvalidDataError("Purchase.lines allows at most 999 lines")
        computed = [line.compute(rates) for line in self.lines]
        totals = summarize(
            zip((line.tax_type for line in self.lines), computed, strict=True), rates
        )
        body = (
            Body("Purchase")
            .text("spplrTin", self.supplier_pin, 11)
            .integer("invcNo", self.invoice_no, required=True, minimum=1)
            .integer("orgInvcNo", self.original_invoice_no, required=True, minimum=0)
            .text("spplrBhfId", self.supplier_branch_id, 2)
            .text("spplrNm", self.supplier_name, 60)
            .integer("spplrInvcNo", self.supplier_invoice_no, minimum=0)
            .code("regTyCd", self.registration_type, required=True)
            .code("pchsTyCd", self.purchase_type, required=True)
            .code("rcptTyCd", self.receipt_type, required=True)
            .code("pmtTyCd", self.payment_type, required=True)
            .code("pchsSttsCd", self.status, required=True)
            .timestamp("cfmDt", self.confirmed_at)
            .day("pchsDt", self.purchase_date, required=True)
            .timestamp("wrhsDt", self.warehoused_at)
            .timestamp("cnclReqDt", self.cancel_requested_at)
            .timestamp("cnclDt", self.cancelled_at)
            .timestamp("rfdDt", self.credit_note_at)
            .integer("totItemCnt", len(self.lines), required=True)
        )
        for key, value in totals.to_api().items():
            body.set(key, value)
        body.text("remark", self.remark, 400).operator(operator)
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
