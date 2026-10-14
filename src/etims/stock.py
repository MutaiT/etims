"""Stock: movements between branches, stock in/out, and stock levels."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from ._payload import Body, Operator, Record
from ._util import Number, json_number, to_decimal
from .codes import RegistrationType, StockIOType, TaxType
from .errors import InvalidDataError
from .tax import LineAmounts, compute_line


@dataclass(frozen=True, kw_only=True)
class StockMovementLine:
    item_seq: int | None
    item_class_code: str | None
    item_code: str | None
    name: str | None
    barcode: str | None
    packaging_unit: str | None
    package_quantity: Decimal | None
    quantity_unit: str | None
    quantity: Decimal | None
    expiry_date: date | None
    unit_price: Decimal | None
    supply_amount: Decimal | None
    discount_amount: Decimal | None
    tax_type: TaxType | None
    taxable_amount: Decimal | None
    tax_amount: Decimal | None
    total_amount: Decimal | None

    @classmethod
    def from_api(cls, r: Record) -> StockMovementLine:
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
            expiry_date=r.day("itemExprDt"),
            unit_price=r.decimal("prc"),
            supply_amount=r.decimal("splyAmt"),
            discount_amount=r.decimal("totDcAmt"),
            tax_type=r.enum(TaxType, "taxTyCd"),
            taxable_amount=r.decimal("taxblAmt"),
            tax_amount=r.decimal("taxAmt"),
            total_amount=r.decimal("totAmt"),
        )


@dataclass(frozen=True, kw_only=True)
class StockMovement:
    """Stock moved to your branch, e.g. from head office (``get_stock_movements``)."""

    customer_pin: str | None
    customer_branch_id: str | None
    sar_no: int | None
    occurred_on: date | None
    total_item_count: int | None
    total_taxable: Decimal | None
    total_tax: Decimal | None
    total_amount: Decimal | None
    remark: str | None
    lines: list[StockMovementLine]

    @classmethod
    def from_api(cls, r: Record) -> StockMovement:
        return cls(
            customer_pin=r.text("custTin"),
            customer_branch_id=r.text("custBhfId"),
            sar_no=r.integer("sarNo"),
            occurred_on=r.day("ocrnDt"),
            total_item_count=r.integer("totItemCnt"),
            total_taxable=r.decimal("totTaxblAmt"),
            total_tax=r.decimal("totTaxAmt"),
            total_amount=r.decimal("totAmt"),
            remark=r.text("remark"),
            lines=[StockMovementLine.from_api(line) for line in r.records("itemList")],
        )


@dataclass(frozen=True, kw_only=True)
class StockIOLine:
    """One item moved in or out of stock. ``unit_price`` includes VAT."""

    item_class_code: str
    name: str
    quantity: Number
    unit_price: Number
    tax_type: TaxType
    packaging_unit: str = "NT"
    quantity_unit: str = "U"
    package_quantity: Number | None = None
    discount_amount: Number = 0
    item_code: str | None = None
    barcode: str | None = None
    expiry_date: date | None = None
    amounts: LineAmounts | None = None

    def compute(self, rates: Mapping[TaxType, Decimal] | None = None) -> LineAmounts:
        if self.amounts is not None:
            return self.amounts
        return compute_line(
            self.quantity,
            self.unit_price,
            self.tax_type,
            discount_amount=self.discount_amount,
            rates=rates,
        )

    def to_api(self, seq: int, amounts: LineAmounts) -> dict[str, Any]:
        if to_decimal(self.quantity) <= 0:
            raise InvalidDataError(f"StockIOLine[{seq}].qty must be greater than zero")
        package = self.quantity if self.package_quantity is None else self.package_quantity
        return (
            Body(f"StockIOLine[{seq}]")
            .integer("itemSeq", seq, required=True, minimum=1, maximum=999)
            .text("itemCd", self.item_code, 20)
            .text("itemClsCd", self.item_class_code, 10, required=True)
            .text("itemNm", self.name, 200, required=True)
            .text("bcd", self.barcode, 20)
            # qty before pkg: pkg defaults to qty, so errors should name qty.
            .text("qtyUnitCd", self.quantity_unit, 5, required=True)
            .amount("qty", self.quantity, required=True)
            .text("pkgUnitCd", self.packaging_unit, 5, required=True)
            .amount("pkg", package, required=True)
            .day("itemExprDt", self.expiry_date)
            .amount("prc", self.unit_price, required=True)
            .amount("splyAmt", amounts.supply_amount, required=True)
            .amount("totDcAmt", amounts.discount_amount, required=True)
            .amount("taxblAmt", amounts.taxable_amount, required=True)
            .code("taxTyCd", self.tax_type, required=True)
            .amount("taxAmt", amounts.tax_amount, required=True)
            .amount("totAmt", amounts.total_amount, required=True)
            .build()
        )


@dataclass(frozen=True, kw_only=True)
class StockIO:
    """Stock going in or out (``save_stock_io``).

    KRA expects every stock movement for a sale to follow the sale itself,
    and stock levels (``save_stock_master``) to follow the movement.
    ``sar_no`` is your own running number for stock movements.
    """

    sar_no: int
    io_type: StockIOType
    lines: Sequence[StockIOLine]
    occurred_on: date
    original_sar_no: int = 0
    registration_type: RegistrationType = RegistrationType.MANUAL
    customer_pin: str | None = None
    customer_name: str | None = None
    customer_branch_id: str | None = None
    remark: str | None = None

    def to_api(
        self, operator: Operator, rates: Mapping[TaxType, Decimal] | None = None
    ) -> dict[str, Any]:
        if not self.lines:
            raise InvalidDataError("StockIO.lines must contain at least one line")
        if len(self.lines) > 999:
            raise InvalidDataError("StockIO.lines allows at most 999 lines")
        computed = [line.compute(rates) for line in self.lines]
        total_taxable = sum((a.taxable_amount for a in computed), Decimal("0.00"))
        total_tax = sum((a.tax_amount for a in computed), Decimal("0.00"))
        total_amount = sum((a.total_amount for a in computed), Decimal("0.00"))
        body = (
            Body("StockIO")
            .integer("sarNo", self.sar_no, required=True, minimum=1)
            .integer("orgSarNo", self.original_sar_no, required=True, minimum=0)
            .code("regTyCd", self.registration_type, required=True)
            .text("custTin", self.customer_pin, 11)
            .text("custNm", self.customer_name, 100)
            .text("custBhfId", self.customer_branch_id, 2)
            .code("sarTyCd", self.io_type, required=True)
            .day("ocrnDt", self.occurred_on, required=True)
            .integer("totItemCnt", len(self.lines), required=True)
            .set("totTaxblAmt", json_number(total_taxable))
            .set("totTaxAmt", json_number(total_tax))
            .set("totAmt", json_number(total_amount))
            .text("remark", self.remark, 400)
            .operator(operator)
        )
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
class StockMaster:
    """The quantity of an item left in stock after a movement (``save_stock_master``)."""

    item_code: str
    remaining_quantity: Number

    def to_api(self, operator: Operator) -> dict[str, Any]:
        return (
            Body("StockMaster")
            .text("itemCd", self.item_code, 20, required=True)
            .amount("rsdQty", self.remaining_quantity, required=True)
            .operator(operator)
            .build()
        )
