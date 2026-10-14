from datetime import date, datetime
from decimal import Decimal

import pytest

from etims import (
    DEFAULT_OPERATOR,
    EAT,
    BranchCustomer,
    BranchInsurance,
    BranchUser,
    InvalidDataError,
    PaymentType,
    Purchase,
    PurchaseSale,
    StockIO,
    StockIOLine,
    StockIOType,
    StockMaster,
    StockMovement,
    TaxType,
)
from etims._payload import Record

SUPPLIER_SALE = {
    "spplrTin": "A123456789Z",
    "spplrNm": "Supplier Ltd",
    "spplrBhfId": "00",
    "spplrInvcNo": 88,
    "rcptTyCd": "S",
    "pmtTyCd": "02",
    "cfmDt": "2026-10-10 09:15:00",
    "salesDt": "20261010",
    "totItemCnt": 1,
    "taxblAmtB": 2320,
    "taxRtB": 16,
    "taxAmtB": 320,
    "totTaxblAmt": 2320,
    "totTaxAmt": 320,
    "totAmt": 2320,
    "itemList": [
        {
            "itemSeq": 1,
            "itemCd": "KE2NTU0000077",
            "itemClsCd": "4322260000",
            "itemNm": "Switch",
            "pkgUnitCd": "NT",
            "pkg": 2,
            "qtyUnitCd": "U",
            "qty": 2,
            "prc": 1160,
            "splyAmt": 2320,
            "dcRt": 0,
            "dcAmt": 0,
            "taxTyCd": "B",
            "taxblAmt": 2320,
            "taxAmt": 320,
            "totAmt": 2320,
        }
    ],
}


def test_supplier_sale_parsing() -> None:
    sale = PurchaseSale.from_api(Record(SUPPLIER_SALE))
    assert sale.confirmed_at == datetime(2026, 10, 10, 9, 15, tzinfo=EAT)
    assert sale.payment_type is PaymentType.CREDIT
    assert sale.taxable_by_type[TaxType.STANDARD] == Decimal(2320)
    assert sale.lines[0].tax_amount == Decimal(320)


def test_confirming_a_supplier_sale_copies_its_figures() -> None:
    sale = PurchaseSale.from_api(Record(SUPPLIER_SALE))
    purchase = Purchase.from_purchase_sale(
        sale,
        invoice_no=5,
        map_item=lambda line: ("KE2NTU0000123", "4322260000") if line.item_code else None,
    )
    body = purchase.to_api(DEFAULT_OPERATOR)
    assert body["invcNo"] == 5
    assert body["spplrTin"] == "A123456789Z"
    assert body["spplrInvcNo"] == 88
    assert (body["regTyCd"], body["pchsTyCd"], body["rcptTyCd"], body["pmtTyCd"]) == (
        "A",
        "N",
        "P",
        "02",
    )
    assert body["pchsDt"] == "20261010"
    assert body["cfmDt"] == "20261010091500"
    assert body["taxblAmtB"] == 2320
    assert body["taxAmtB"] == 320
    (line,) = body["itemList"]
    assert line["itemCd"] == "KE2NTU0000123"
    assert line["spplrItemCd"] == "KE2NTU0000077"
    assert line["taxAmt"] == 320


def test_supplier_lines_without_amounts_are_refused() -> None:
    incomplete = dict(SUPPLIER_SALE, itemList=[{"itemSeq": 1, "itemNm": "?"}])
    with pytest.raises(InvalidDataError, match="Supplier line 1"):
        Purchase.from_purchase_sale(PurchaseSale.from_api(Record(incomplete)), invoice_no=1)


def test_purchase_needs_lines() -> None:
    purchase = Purchase(
        invoice_no=1, lines=[], purchase_date=date(2026, 10, 1), payment_type=PaymentType.CASH
    )
    with pytest.raises(InvalidDataError, match="at least one line"):
        purchase.to_api(DEFAULT_OPERATOR)


def test_stock_io_body() -> None:
    stock_io = StockIO(
        sar_no=3,
        io_type=StockIOType.SALE,
        occurred_on=date(2026, 10, 14),
        lines=[
            StockIOLine(
                item_class_code="4322260000",
                item_code="KE2NTU0000123",
                name="Switch",
                quantity=2,
                unit_price=1160,
                tax_type=TaxType.STANDARD,
                discount_amount=232,
            )
        ],
    )
    body = stock_io.to_api(DEFAULT_OPERATOR)
    assert body["sarNo"] == 3
    assert body["orgSarNo"] == 0
    assert body["sarTyCd"] == "11"
    assert body["ocrnDt"] == "20261014"
    assert body["totTaxblAmt"] == 2088
    assert body["totTaxAmt"] == 288
    assert body["itemList"][0]["totDcAmt"] == 232


def test_stock_movement_parsing() -> None:
    movement = StockMovement.from_api(
        Record(
            {
                "custTin": "P051234567A",
                "custBhfId": "00",
                "sarNo": 6,
                "ocrnDt": "20261001",
                "totAmt": 1160,
                "itemList": [
                    {"itemSeq": 1, "itemCd": "X", "qty": 1, "totDcAmt": 0, "taxTyCd": "B"}
                ],
            }
        )
    )
    assert movement.occurred_on == date(2026, 10, 1)
    assert movement.lines[0].tax_type is TaxType.STANDARD
    assert movement.lines[0].discount_amount == Decimal(0)


def test_stock_master_body() -> None:
    body = StockMaster(item_code="KE2NTU0000123", remaining_quantity=Decimal("12.5")).to_api(
        DEFAULT_OPERATOR
    )
    assert body["rsdQty"] == 12.5
    assert body["regrNm"] == "etims Python SDK"


def test_branch_customer() -> None:
    body = BranchCustomer(customer_no="000000001", customer_pin="A123456789Z", name="Acme").to_api(
        DEFAULT_OPERATOR
    )
    assert body["custNo"] == "000000001"
    assert body["useYn"] == "Y"
    with pytest.raises(InvalidDataError, match="not a valid KRA PIN"):
        BranchCustomer(customer_no="1", customer_pin="bad", name="Acme").to_api(DEFAULT_OPERATOR)


def test_branch_user_hides_its_password() -> None:
    user = BranchUser(user_id="cashier1", name="Cashier", password="s3cret")
    assert "s3cret" not in repr(user)
    assert user.to_api(DEFAULT_OPERATOR)["pwd"] == "s3cret"


def test_branch_insurance_rate_is_a_percentage() -> None:
    assert (
        BranchInsurance(code="INS01", name="Cover Ltd", rate=20).to_api(DEFAULT_OPERATOR)["isrcRt"]
        == 20
    )
    with pytest.raises(InvalidDataError, match="at most 100"):
        BranchInsurance(code="INS01", name="Cover Ltd", rate=120).to_api(DEFAULT_OPERATOR)
