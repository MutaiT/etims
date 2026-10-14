"""Items (products and services) and imported goods."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from ._payload import Body, Operator, Record
from ._util import Number
from .codes import ImportItemStatus, ProductType, TaxType
from .errors import InvalidDataError


def make_item_code(
    product_type: ProductType,
    packaging_unit: str,
    quantity_unit: str,
    sequence: int,
    *,
    origin_country: str = "KE",
) -> str:
    """Build an item code the way the specification (4.19) describes.

    ``make_item_code(ProductType.FINISHED_PRODUCT, "NT", "BA", 12)`` gives
    ``"KE2NTBA0000012"``: origin country, product type, packaging unit,
    quantity unit, then a 7-digit running number. KRA only requires item
    codes to be unique per taxpayer; this is its recommended pattern.
    """
    if not 1 <= sequence <= 9_999_999:
        raise InvalidDataError("Item code sequence must be between 1 and 9999999")
    code = f"{origin_country}{product_type.value}{packaging_unit}{quantity_unit}{sequence:07d}"
    if len(code) > 20:
        raise InvalidDataError(f"Item code {code!r} is longer than 20 characters")
    return code


@dataclass(frozen=True, kw_only=True)
class Item:
    """A product or service to register with KRA (``save_item``).

    Items must be registered before they can be sold. Saving an existing
    ``item_code`` again updates it.
    """

    item_code: str
    item_class_code: str
    product_type: ProductType
    name: str
    packaging_unit: str
    quantity_unit: str
    tax_type: TaxType
    default_price: Number
    origin_country: str = "KE"
    standard_name: str | None = None
    batch_no: str | None = None
    barcode: str | None = None
    group_prices: tuple[Number | None, ...] = ()
    additional_info: str | None = None
    safety_quantity: Number | None = None
    insurance_applicable: bool = False
    active: bool = True

    def to_api(self, operator: Operator) -> dict[str, Any]:
        if len(self.group_prices) > 5:
            raise InvalidDataError("Item.group_prices allows at most 5 price levels")
        body = (
            Body("Item")
            .text("itemCd", self.item_code, 20, required=True)
            .text("itemClsCd", self.item_class_code, 10, required=True)
            .code("itemTyCd", self.product_type, required=True)
            .text("itemNm", self.name, 200, required=True)
            .text("itemStdNm", self.standard_name, 200)
            .text("orgnNatCd", self.origin_country, 5, required=True)
            .text("pkgUnitCd", self.packaging_unit, 5, required=True)
            .text("qtyUnitCd", self.quantity_unit, 5, required=True)
            .code("taxTyCd", self.tax_type, required=True)
            .text("btchNo", self.batch_no, 10)
            .text("bcd", self.barcode, 20)
            .amount("dftPrc", self.default_price, required=True)
        )
        prices = list(self.group_prices) + [None] * (5 - len(self.group_prices))
        for level, price in enumerate(prices, start=1):
            body.amount(f"grpPrcL{level}", price)
        return (
            body.text("addInfo", self.additional_info, 7)
            .amount("sftyQty", self.safety_quantity)
            .flag("isrcAplcbYn", self.insurance_applicable)
            .flag("useYn", self.active)
            .operator(operator)
            .build()
        )


@dataclass(frozen=True, kw_only=True)
class RegisteredItem:
    """An item as KRA has it on record (``get_items``)."""

    tin: str | None
    item_code: str
    item_class_code: str | None
    product_type: ProductType | None
    name: str | None
    standard_name: str | None
    origin_country: str | None
    packaging_unit: str | None
    quantity_unit: str | None
    tax_type: TaxType | None
    batch_no: str | None
    registered_branch_id: str | None
    barcode: str | None
    default_price: Decimal | None
    group_prices: tuple[Decimal | None, ...]
    additional_info: str | None
    safety_quantity: Decimal | None
    insurance_applicable: bool | None
    kra_modified: bool | None
    active: bool | None

    @classmethod
    def from_api(cls, r: Record) -> RegisteredItem:
        return cls(
            tin=r.text("tin"),
            item_code=r.text("itemCd") or "",
            item_class_code=r.text("itemClsCd"),
            product_type=r.enum(ProductType, "itemTyCd"),
            name=r.text("itemNm"),
            standard_name=r.text("itemStdNm"),
            origin_country=r.text("orgnNatCd"),
            packaging_unit=r.text("pkgUnitCd"),
            quantity_unit=r.text("qtyUnitCd"),
            tax_type=r.enum(TaxType, "taxTyCd"),
            batch_no=r.text("btchNo"),
            registered_branch_id=r.text("regBhfId"),
            barcode=r.text("bcd"),
            default_price=r.decimal("dftPrc"),
            group_prices=tuple(r.decimal(f"grpPrcL{n}") for n in range(1, 6)),
            additional_info=r.text("addInfo"),
            safety_quantity=r.decimal("sftyQty"),
            insurance_applicable=r.flag("isrcAplcbYn"),
            kra_modified=r.flag("rraModYn"),
            active=r.flag("useYn"),
        )


@dataclass(frozen=True, kw_only=True)
class ImportedItem:
    """An import declared under your PIN at customs (``get_imported_items``)."""

    task_code: str | None
    declaration_date: date | None
    item_seq: int | None
    declaration_no: str | None
    hs_code: str | None
    name: str | None
    status: ImportItemStatus | None
    origin_country: str | None
    export_country: str | None
    package_quantity: Decimal | None
    packaging_unit: str | None
    quantity: Decimal | None
    quantity_unit: str | None
    gross_weight: Decimal | None
    net_weight: Decimal | None
    supplier_name: str | None
    agent_name: str | None
    invoice_amount: Decimal | None
    invoice_currency: str | None
    exchange_rate: Decimal | None

    @classmethod
    def from_api(cls, r: Record) -> ImportedItem:
        return cls(
            task_code=r.text("taskCd"),
            declaration_date=r.day("dclDe"),
            item_seq=r.integer("itemSeq"),
            declaration_no=r.text("dclNo"),
            hs_code=r.text("hsCd"),
            name=r.text("itemNm"),
            # The spec spells this both "imptItemsttsCd" and "imptItemSttsCd".
            status=r.enum(ImportItemStatus, "imptItemSttsCd", "imptItemsttsCd"),
            origin_country=r.text("orgnNatCd"),
            export_country=r.text("exptNatCd"),
            package_quantity=r.decimal("pkg"),
            packaging_unit=r.text("pkgUnitCd"),
            quantity=r.decimal("qty"),
            quantity_unit=r.text("qtyUnitCd"),
            gross_weight=r.decimal("totWt"),
            net_weight=r.decimal("netWt"),
            supplier_name=r.text("spplrNm"),
            agent_name=r.text("agntNm"),
            invoice_amount=r.decimal("invcFcurAmt"),
            invoice_currency=r.text("invcFcurCd"),
            exchange_rate=r.decimal("invcFcurExcrt"),
        )


@dataclass(frozen=True, kw_only=True)
class ImportedItemUpdate:
    """Approve or reject an imported item, linking it to one of your items."""

    task_code: str
    declaration_date: date
    item_seq: int
    hs_code: str
    item_class_code: str
    item_code: str
    status: ImportItemStatus
    remark: str | None = None

    @classmethod
    def for_import(
        cls,
        imported: ImportedItem,
        *,
        item_class_code: str,
        item_code: str,
        status: ImportItemStatus,
        remark: str | None = None,
    ) -> ImportedItemUpdate:
        """Build the update for an item returned by ``get_imported_items()``."""
        if (
            imported.task_code is None
            or imported.declaration_date is None
            or imported.item_seq is None
            or imported.hs_code is None
        ):
            raise InvalidDataError(
                "The imported item is missing its task code, declaration date, "
                "item sequence or HS code"
            )
        return cls(
            task_code=imported.task_code,
            declaration_date=imported.declaration_date,
            item_seq=imported.item_seq,
            hs_code=imported.hs_code,
            item_class_code=item_class_code,
            item_code=item_code,
            status=status,
            remark=remark,
        )

    def to_api(self, operator: Operator) -> dict[str, Any]:
        return (
            Body("ImportedItemUpdate")
            .text("taskCd", self.task_code, 50, required=True)
            .day("dclDe", self.declaration_date, required=True)
            .integer("itemSeq", self.item_seq, required=True, minimum=1)
            .text("hsCd", self.hs_code, 17, required=True)
            .text("itemClsCd", self.item_class_code, 10, required=True)
            .text("itemCd", self.item_code, 20, required=True)
            # Spelling from the spec's JSON sample; its table says "imptItemsttsCd".
            .code("imptItemSttsCd", self.status, required=True)
            .text("remark", self.remark, 400)
            .operator(operator, registrant=False)
            .build()
        )
