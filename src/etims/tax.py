"""VAT arithmetic the way eTIMS expects it.

eTIMS amounts are VAT-inclusive. In the specification's sales example a
taxable amount of 10,500 at 18% carries 1,602 of tax (10,500 x 18/118),
and ``totAmt`` equals ``taxblAmt``. So for Kenya's standard 16% rate:

    supply   = quantity x unit price (VAT included)
    taxable  = supply - discount
    tax      = taxable x 16 / 116          (the VAT already inside the price)
    total    = taxable

Each line is rounded to cents (half up) and the invoice totals are the sums
of the rounded lines, so the header always agrees with the lines.

If your system stores prices without VAT, convert them with
``gross_price()`` or pass the line amounts your system already computed
(see ``SaleLine``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ._util import Number, json_number, money, to_decimal
from .codes import DEFAULT_TAX_RATES, TaxType

_ZERO = Decimal("0.00")


@dataclass(frozen=True)
class LineAmounts:
    """The amount fields of one invoice line, as KRA wants them."""

    supply_amount: Decimal  # splyAmt
    discount_amount: Decimal  # dcAmt
    taxable_amount: Decimal  # taxblAmt (VAT included)
    tax_amount: Decimal  # taxAmt (the VAT inside taxblAmt)

    @property
    def total_amount(self) -> Decimal:  # totAmt
        return self.taxable_amount


def rate_for(tax_type: TaxType, rates: Mapping[TaxType, Decimal] | None = None) -> Decimal:
    table = DEFAULT_TAX_RATES if rates is None else rates
    try:
        return table[tax_type]
    except KeyError:
        raise ValueError(f"No tax rate configured for tax type {tax_type.value}") from None


def tax_included(amount: Number, rate: Number) -> Decimal:
    """The VAT contained in a VAT-inclusive amount, rounded to cents."""
    amount_d, rate_d = to_decimal(amount), to_decimal(rate)
    if rate_d == 0:
        return _ZERO
    return money(amount_d * rate_d / (100 + rate_d))


def gross_price(
    net_price: Number, tax_type: TaxType, rates: Mapping[TaxType, Decimal] | None = None
) -> Decimal:
    """Convert a price without VAT to the VAT-inclusive price eTIMS expects."""
    rate = rate_for(tax_type, rates)
    return money(to_decimal(net_price) * (100 + rate) / 100)


def compute_line(
    quantity: Number,
    unit_price: Number,
    tax_type: TaxType,
    *,
    discount_rate: Number = 0,
    discount_amount: Number | None = None,
    rates: Mapping[TaxType, Decimal] | None = None,
) -> LineAmounts:
    """Compute a line from quantity and VAT-inclusive unit price.

    The discount is either a rate in percent (sales and purchases) or a fixed
    ``discount_amount`` (stock movements); a given amount takes precedence.
    """
    supply = money(to_decimal(quantity) * to_decimal(unit_price))
    if discount_amount is not None:
        discount = money(discount_amount)
    else:
        discount = money(supply * to_decimal(discount_rate) / 100)
    taxable = supply - discount
    return LineAmounts(
        supply_amount=supply,
        discount_amount=discount,
        taxable_amount=taxable,
        tax_amount=tax_included(taxable, rate_for(tax_type, rates)),
    )


@dataclass(frozen=True)
class Totals:
    """Invoice totals by tax type (the taxblAmtX / taxRtX / taxAmtX fields)."""

    taxable_by_type: Mapping[TaxType, Decimal]
    tax_by_type: Mapping[TaxType, Decimal]
    rates: Mapping[TaxType, Decimal]

    @property
    def total_taxable(self) -> Decimal:
        return sum(self.taxable_by_type.values(), _ZERO)

    @property
    def total_tax(self) -> Decimal:
        return sum(self.tax_by_type.values(), _ZERO)

    @property
    def total_amount(self) -> Decimal:
        return self.total_taxable

    def to_api(self) -> dict[str, Any]:
        """All fifteen per-type fields plus the three totals, in KRA's names."""
        body: dict[str, Any] = {}
        for tax_type in TaxType:
            suffix = tax_type.value
            body[f"taxblAmt{suffix}"] = json_number(self.taxable_by_type[tax_type])
            body[f"taxRt{suffix}"] = json_number(self.rates[tax_type])
            body[f"taxAmt{suffix}"] = json_number(self.tax_by_type[tax_type])
        body["totTaxblAmt"] = json_number(self.total_taxable)
        body["totTaxAmt"] = json_number(self.total_tax)
        body["totAmt"] = json_number(self.total_amount)
        return body


def summarize(
    lines: Iterable[tuple[TaxType, LineAmounts]],
    rates: Mapping[TaxType, Decimal] | None = None,
) -> Totals:
    """Sum rounded line amounts into per-tax-type totals."""
    table = DEFAULT_TAX_RATES if rates is None else rates
    taxable = dict.fromkeys(TaxType, _ZERO)
    tax = dict.fromkeys(TaxType, _ZERO)
    for tax_type, amounts in lines:
        taxable[tax_type] += amounts.taxable_amount
        tax[tax_type] += amounts.tax_amount
    return Totals(
        taxable_by_type=taxable,
        tax_by_type=tax,
        rates={t: rate_for(t, table) for t in TaxType},
    )
