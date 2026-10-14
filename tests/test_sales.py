from datetime import datetime, timezone
from decimal import Decimal

import pytest
from conftest import BHF, TIN, make_sale

from etims import (
    DEFAULT_OPERATOR,
    DEFAULT_TAX_RATES,
    EAT,
    CreditNoteReason,
    Environment,
    InvalidDataError,
    LineAmounts,
    Operator,
    PaymentType,
    ReceiptDetails,
    ReceiptType,
    SaleLine,
    SaleReceipt,
    TaxType,
)


def test_sale_body_matches_the_specification() -> None:
    sale = make_sale(
        customer_pin="A123456789Z", customer_name="Acme Ltd", customer_phone="0712345678"
    )
    body = sale.to_api(Operator(id="u42", name="Jane Doe"))

    assert "tin" not in body
    assert "cmcKey" not in body
    assert body["trdInvcNo"] == "INV/2026/0001"
    assert body["invcNo"] == 1
    assert body["orgInvcNo"] == 0
    assert body["custTin"] == "A123456789Z"
    assert body["custNm"] == "Acme Ltd"
    assert (body["salesTyCd"], body["rcptTyCd"], body["pmtTyCd"], body["salesSttsCd"]) == (
        "N",
        "S",
        "01",
        "02",
    )
    assert body["cfmDt"] == "20261014123005"
    assert body["salesDt"] == "20261014"
    assert body["rfdDt"] is None
    assert body["rfdRsnCd"] is None
    assert body["totItemCnt"] == 1
    assert body["taxblAmtB"] == 1160
    assert body["taxAmtB"] == 160
    assert body["taxRtB"] == 16
    assert body["totTaxblAmt"] == body["totAmt"] == 1160
    assert body["totTaxAmt"] == 160
    assert body["prchrAcptcYn"] == "N"
    assert (body["regrId"], body["regrNm"], body["modrId"], body["modrNm"]) == (
        "u42",
        "Jane Doe",
        "u42",
        "Jane Doe",
    )
    assert body["receipt"] == {
        "custTin": "A123456789Z",
        "custMblNo": "0712345678",
        "rcptPbctDt": "20261014123005",
        "trdeNm": None,
        "adrs": None,
        "topMsg": None,
        "btmMsg": None,
        "prchrAcptcYn": "N",
    }
    (line,) = body["itemList"]
    assert line == {
        "itemSeq": 1,
        "itemClsCd": "5059690800",
        "itemCd": "KE2NTU0000001",
        "itemNm": "Integration consulting",
        "bcd": None,
        "pkgUnitCd": "NT",
        "pkg": 2,
        "qtyUnitCd": "U",
        "qty": 2,
        "prc": 580,
        "splyAmt": 1160,
        "dcRt": 0,
        "dcAmt": 0,
        "isrccCd": None,
        "isrccNm": None,
        "isrcRt": None,
        "isrcAmt": None,
        "taxTyCd": "B",
        "taxblAmt": 1160,
        "taxAmt": 160,
        "totAmt": 1160,
    }


def test_dates_are_sent_in_kenyan_time() -> None:
    sale = make_sale(confirmed_at=datetime(2026, 12, 31, 22, 30, tzinfo=timezone.utc))
    body = sale.to_api(DEFAULT_OPERATOR)
    assert body["cfmDt"] == "20270101013000"
    assert body["salesDt"] == "20270101"


def test_credit_note() -> None:
    note = make_sale(
        invoice_no=2,
        receipt_type=ReceiptType.CREDIT_NOTE,
        original_invoice_no=1,
        credit_note_reason=CreditNoteReason.REFUND,
        payment_type=PaymentType.MOBILE_MONEY,
    )
    body = note.to_api(DEFAULT_OPERATOR)
    assert body["rcptTyCd"] == "R"
    assert body["orgInvcNo"] == 1
    assert body["rfdRsnCd"] == "06"
    assert body["rfdDt"] == body["cfmDt"]
    assert body["pmtTyCd"] == "06"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {
                "receipt_type": ReceiptType.CREDIT_NOTE,
                "credit_note_reason": CreditNoteReason.REFUND,
            },
            "original_invoice_no",
        ),
        (
            {
                "receipt_type": ReceiptType.CREDIT_NOTE,
                "original_invoice_no": 1,
                "invoice_no": 1,
                "credit_note_reason": CreditNoteReason.REFUND,
            },
            "its own invoice_no",
        ),
        (
            {"receipt_type": ReceiptType.CREDIT_NOTE, "original_invoice_no": 1, "invoice_no": 2},
            "credit_note_reason",
        ),
        ({"original_invoice_no": 5}, "only for credit notes"),
        ({"invoice_no": None}, "SaleOutbox"),
        ({"invoice_no": 0}, "1 or more"),
        ({"lines": []}, "at least one line"),
        ({"customer_pin": "12345"}, "not a valid KRA PIN"),
        ({"trader_invoice_no": "X" * 51}, "Sale.trdInvcNo must be at most 50 characters"),
        (
            {"receipt": ReceiptDetails(top_message="Thank you for shopping")},
            "topMsg must be at most 20",
        ),
    ],
)
def test_invalid_sales_are_refused_before_sending(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(InvalidDataError, match=message):
        make_sale(**overrides).to_api(DEFAULT_OPERATOR)


def _line(**overrides: object) -> SaleLine:
    fields: dict[str, object] = {
        "item_code": "KE2NTU0000001",
        "item_class_code": "5059690800",
        "name": "Item",
        "quantity": 1,
        "unit_price": 100,
        "tax_type": TaxType.STANDARD,
    }
    fields.update(overrides)
    return SaleLine(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("line", "message"),
    [
        (_line(quantity=Decimal("1.125")), "qty must have at most 2 decimal places"),
        (_line(quantity=0), "greater than zero"),
        (_line(unit_price=-1), "must not be negative"),
        (_line(name="x" * 201), "itemNm must be at most 200"),
        (_line(item_code=""), "itemCd is required"),
    ],
)
def test_invalid_lines_are_refused(line: SaleLine, message: str) -> None:
    with pytest.raises(InvalidDataError, match=message):
        make_sale(lines=[line]).to_api(DEFAULT_OPERATOR)


def test_amounts_from_your_own_system_are_sent_unchanged() -> None:
    mine = LineAmounts(
        supply_amount=Decimal("1160.00"),
        discount_amount=Decimal("0.00"),
        taxable_amount=Decimal("1160.00"),
        tax_amount=Decimal("160.01"),
    )
    body = make_sale(lines=[_line(unit_price=1160, amounts=mine)]).to_api(DEFAULT_OPERATOR)
    assert body["itemList"][0]["taxAmt"] == 160.01
    assert body["taxAmtB"] == 160.01


def test_custom_tax_rates() -> None:
    rates = {**DEFAULT_TAX_RATES, TaxType.STANDARD: Decimal(14)}
    body = make_sale(lines=[_line(unit_price=114)]).to_api(DEFAULT_OPERATOR, rates)
    assert body["taxRtB"] == 14
    assert body["taxAmtB"] == 14


def test_mixed_tax_types_fill_each_bucket() -> None:
    body = make_sale(
        lines=[_line(unit_price=1160), _line(unit_price=108, tax_type=TaxType.REDUCED)]
    ).to_api(DEFAULT_OPERATOR)
    assert (body["taxblAmtB"], body["taxAmtB"]) == (1160, 160)
    assert (body["taxblAmtE"], body["taxAmtE"]) == (108, 8)
    assert [line["itemSeq"] for line in body["itemList"]] == [1, 2]


def test_receipt_parsing_tolerates_kra_quirks() -> None:
    # The specification's sample has keys with trailing spaces and numbers as strings.
    receipt = SaleReceipt.from_api(
        {
            "curRcptNo ": "7",
            "totRcptNo ": "19",
            "intrlData": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
            "rcptSign": "SIGN1234SIGN1234",
            "sdcDateTime": "20261014123010",
        },
        invoice_no=7,
        tin=TIN,
        branch_id=BHF,
        environment=Environment.SANDBOX,
    )
    assert receipt.receipt_no == 7
    assert receipt.total_receipt_no == 19
    assert receipt.signed_at == datetime(2026, 10, 14, 12, 30, 10, tzinfo=EAT)
    assert receipt.qr_data == f"{TIN}00SIGN1234SIGN1234"
    assert receipt.qr_url == (
        "https://etims-sbx.kra.go.ke/common/link/etims/receipt/indexEtimsReceiptData"
        f"?Data={TIN}00SIGN1234SIGN1234"
    )


def test_receipt_without_signature_has_no_qr() -> None:
    receipt = SaleReceipt.from_api(
        {}, invoice_no=1, tin=TIN, branch_id=BHF, environment=Environment.PRODUCTION
    )
    assert receipt.qr_data is None
    assert receipt.qr_url is None
