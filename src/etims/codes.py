"""KRA eTIMS code values, from chapter 4 of the OSCU specification (v2.0).

Small, fixed lists are enums here. Large or changing lists (countries,
packaging and quantity units, currencies, item classifications) come from
the API: see ``OscuClient.get_codes()`` and ``OscuClient.get_item_classes()``.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from enum import Enum
from types import MappingProxyType


class TaxType(str, Enum):
    """Tax type (code class 04). Rates are in ``DEFAULT_TAX_RATES``."""

    EXEMPT = "A"
    STANDARD = "B"  # 16%
    ZERO_RATED = "C"  # 0%
    NON_VAT = "D"
    REDUCED = "E"  # 8%


#: Rates from the specification. KRA publishes the live rates in code class 04
#: (``userDfnCd1``); pass updated rates to ``OscuClient(tax_rates=...)`` if they change.
DEFAULT_TAX_RATES: Mapping[TaxType, Decimal] = MappingProxyType(
    {
        TaxType.EXEMPT: Decimal(0),
        TaxType.STANDARD: Decimal(16),
        TaxType.ZERO_RATED: Decimal(0),
        TaxType.NON_VAT: Decimal(0),
        TaxType.REDUCED: Decimal(8),
    }
)


class TaxpayerStatus(str, Enum):
    """Taxpayer status (code class 15)."""

    ACTIVE = "A"
    INACTIVE = "D"


class ProductType(str, Enum):
    """Product type (code class 24). Also the 3rd character of an item code."""

    RAW_MATERIAL = "1"
    FINISHED_PRODUCT = "2"
    SERVICE = "3"  # service without stock


class SalesType(str, Enum):
    """Transaction type for sales and purchases (code class 14)."""

    COPY = "C"
    NORMAL = "N"
    PROFORMA = "P"
    TRAINING = "T"


class ReceiptType(str, Enum):
    """Sales receipt type (code class 37)."""

    SALE = "S"
    CREDIT_NOTE = "R"


class PurchaseReceiptType(str, Enum):
    """Purchase receipt type (code class 38)."""

    PURCHASE = "P"
    CREDIT_NOTE = "R"


class PaymentType(str, Enum):
    """Payment method (code class 07)."""

    CASH = "01"
    CREDIT = "02"
    CASH_CREDIT = "03"
    BANK_CHECK = "04"
    CARD = "05"
    MOBILE_MONEY = "06"
    OTHER = "07"


class TransactionStatus(str, Enum):
    """Transaction progress (code class 11)."""

    WAIT_FOR_APPROVAL = "01"
    APPROVED = "02"
    CANCEL_REQUESTED = "03"
    CANCELED = "04"
    CREDIT_NOTE_GENERATED = "05"
    TRANSFERRED = "06"


class RegistrationType(str, Enum):
    """Registration type (code class 31)."""

    AUTOMATIC = "A"
    MANUAL = "M"


class StockIOType(str, Enum):
    """Stock movement type (code class 12). 0x codes are incoming, 1x outgoing."""

    IMPORT = "01"
    PURCHASE = "02"
    RETURN_IN = "03"
    STOCK_MOVEMENT_IN = "04"
    PROCESSING_IN = "05"
    ADJUSTMENT_IN = "06"
    SALE = "11"
    RETURN_OUT = "12"
    STOCK_MOVEMENT_OUT = "13"
    PROCESSING_OUT = "14"
    DISCARDING = "15"
    ADJUSTMENT_OUT = "16"


class ImportItemStatus(str, Enum):
    """Imported item status (code class 26)."""

    UNSENT = "1"
    WAITING = "2"
    APPROVED = "3"
    CANCELLED = "4"


class CreditNoteReason(str, Enum):
    """Credit note reason (code class 32)."""

    MISSING_QUANTITY = "01"
    MISSING_DATA = "02"
    DAMAGED = "03"
    WASTED = "04"
    RAW_MATERIAL_SHORTAGE = "05"
    REFUND = "06"


class CodeClassId(str, Enum):
    """Code class IDs for looking up lists returned by ``get_codes()``."""

    TAX_TYPE = "04"
    COUNTRY = "05"
    PAYMENT_TYPE = "07"
    QUANTITY_UNIT = "10"
    TRANSACTION_STATUS = "11"
    STOCK_IO_TYPE = "12"
    SALES_TYPE = "14"
    TAXPAYER_STATUS = "15"
    PACKAGING_UNIT = "17"
    PRODUCT_TYPE = "24"
    IMPORT_ITEM_STATUS = "26"
    REGISTRATION_TYPE = "31"
    CREDIT_NOTE_REASON = "32"
    CURRENCY = "33"
    SALES_RECEIPT_TYPE = "37"
    PURCHASE_RECEIPT_TYPE = "38"
