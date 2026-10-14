from decimal import Decimal

import pytest

from etims import DEFAULT_TAX_RATES, TaxType, compute_line, gross_price, tax_included
from etims.tax import summarize


@pytest.mark.parametrize(
    ("amount", "rate", "expected"),
    [
        ("1160", 16, "160.00"),
        ("5000", 16, "689.66"),
        ("108", 8, "8.00"),
        ("100", 0, "0.00"),
        # The specification's own example: 10,500 at 18% carries 1,601.69 of VAT.
        ("10500", 18, "1601.69"),
    ],
)
def test_vat_is_extracted_from_inclusive_amounts(amount: str, rate: int, expected: str) -> None:
    assert tax_included(amount, rate) == Decimal(expected)


def test_gross_price_adds_vat_to_net_prices() -> None:
    assert gross_price(1000, TaxType.STANDARD) == Decimal("1160.00")
    assert gross_price(100, TaxType.REDUCED) == Decimal("108.00")
    assert gross_price(250, TaxType.EXEMPT) == Decimal("250.00")


def test_line_with_discount_rate() -> None:
    line = compute_line(2, "580", TaxType.STANDARD, discount_rate=10)
    assert line.supply_amount == Decimal("1160.00")
    assert line.discount_amount == Decimal("116.00")
    assert line.taxable_amount == Decimal("1044.00")
    assert line.tax_amount == Decimal("144.00")
    assert line.total_amount == line.taxable_amount


def test_fixed_discount_amount_takes_precedence() -> None:
    line = compute_line(1, "1160", TaxType.STANDARD, discount_rate=50, discount_amount="116")
    assert line.taxable_amount == Decimal("1044.00")


def test_totals_by_tax_type_sum_the_rounded_lines() -> None:
    totals = summarize(
        [
            (TaxType.STANDARD, compute_line(1, "1160", TaxType.STANDARD)),
            (TaxType.STANDARD, compute_line(3, "0.35", TaxType.STANDARD)),
            (TaxType.REDUCED, compute_line(1, "108", TaxType.REDUCED)),
            (TaxType.EXEMPT, compute_line(1, "500", TaxType.EXEMPT)),
        ]
    )
    assert totals.taxable_by_type[TaxType.STANDARD] == Decimal("1161.05")
    # 160.00 + 0.14 (1.05 x 16/116 = 0.1448): lines are rounded, then summed
    assert totals.tax_by_type[TaxType.STANDARD] == Decimal("160.14")
    assert totals.total_taxable == Decimal("1769.05")
    assert totals.total_tax == Decimal("168.14")
    body = totals.to_api()
    assert len(body) == 18
    assert body["taxRtB"] == 16
    assert body["taxRtE"] == 8
    assert body["taxAmtC"] == 0
    assert body["totAmt"] == body["totTaxblAmt"] == 1769.05


def test_custom_rates_are_used() -> None:
    rates = {**DEFAULT_TAX_RATES, TaxType.STANDARD: Decimal(14)}
    assert compute_line(1, "114", TaxType.STANDARD, rates=rates).tax_amount == Decimal("14.00")


def test_missing_rate_is_reported() -> None:
    with pytest.raises(ValueError, match="No tax rate configured for tax type E"):
        compute_line(1, "10", TaxType.REDUCED, rates={TaxType.STANDARD: Decimal(16)})
