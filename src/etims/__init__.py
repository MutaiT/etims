"""Python client for KRA eTIMS through the Online Sales Control Unit (OSCU) API.

Talks to the Kenya Revenue Authority directly: no middleware account needed.
"""

import logging

from ._payload import Operator
from ._util import EAT, is_valid_pin
from .branches import BranchCustomer, BranchInsurance, BranchUser
from .client import DEFAULT_OPERATOR, FULL_SYNC, OscuClient, RetryPolicy
from .codes import (
    DEFAULT_TAX_RATES,
    CodeClassId,
    CreditNoteReason,
    ImportItemStatus,
    PaymentType,
    ProductType,
    PurchaseReceiptType,
    ReceiptType,
    RegistrationType,
    SalesType,
    StockIOType,
    TaxpayerStatus,
    TaxType,
    TransactionStatus,
)
from .environment import Environment
from .errors import (
    RESULT_CODES,
    AuthenticationError,
    ConfigurationError,
    ConnectionFailedError,
    DeviceError,
    DuplicateError,
    EtimsError,
    InvalidDataError,
    KraError,
    KraServerError,
    ProtocolError,
    RateLimitError,
    RequestError,
    ResponseTimeoutError,
    SequenceError,
    TransportError,
)
from .items import ImportedItem, ImportedItemUpdate, Item, RegisteredItem, make_item_code
from .outbox import FlushReport, OutboxEntry, OutboxStatus, SaleOutbox
from .purchases import Purchase, PurchaseLine, PurchaseSale, PurchaseSaleLine
from .reference import (
    Branch,
    Code,
    CodeClass,
    DeviceInfo,
    ItemClass,
    Notice,
    SearchResult,
    Taxpayer,
    find_code_class,
    tax_rates_from_codes,
)
from .sales import ReceiptDetails, Sale, SaleLine, SaleReceipt
from .stock import StockIO, StockIOLine, StockMaster, StockMovement, StockMovementLine
from .tax import LineAmounts, compute_line, gross_price, tax_included

__version__ = "0.1.0"

# Libraries log but leave output to the application.
logging.getLogger("etims").addHandler(logging.NullHandler())

__all__ = [
    "DEFAULT_OPERATOR",
    "DEFAULT_TAX_RATES",
    "EAT",
    "FULL_SYNC",
    "RESULT_CODES",
    "AuthenticationError",
    "Branch",
    "BranchCustomer",
    "BranchInsurance",
    "BranchUser",
    "Code",
    "CodeClass",
    "CodeClassId",
    "ConfigurationError",
    "ConnectionFailedError",
    "CreditNoteReason",
    "DeviceError",
    "DeviceInfo",
    "DuplicateError",
    "Environment",
    "EtimsError",
    "FlushReport",
    "ImportItemStatus",
    "ImportedItem",
    "ImportedItemUpdate",
    "InvalidDataError",
    "Item",
    "ItemClass",
    "KraError",
    "KraServerError",
    "LineAmounts",
    "Notice",
    "Operator",
    "OscuClient",
    "OutboxEntry",
    "OutboxStatus",
    "PaymentType",
    "ProductType",
    "ProtocolError",
    "Purchase",
    "PurchaseLine",
    "PurchaseReceiptType",
    "PurchaseSale",
    "PurchaseSaleLine",
    "RateLimitError",
    "ReceiptDetails",
    "ReceiptType",
    "RegisteredItem",
    "RegistrationType",
    "RequestError",
    "ResponseTimeoutError",
    "RetryPolicy",
    "Sale",
    "SaleLine",
    "SaleOutbox",
    "SaleReceipt",
    "SalesType",
    "SearchResult",
    "SequenceError",
    "StockIO",
    "StockIOLine",
    "StockIOType",
    "StockMaster",
    "StockMovement",
    "StockMovementLine",
    "TaxType",
    "Taxpayer",
    "TaxpayerStatus",
    "TransactionStatus",
    "TransportError",
    "__version__",
    "compute_line",
    "find_code_class",
    "gross_price",
    "is_valid_pin",
    "make_item_code",
    "tax_included",
    "tax_rates_from_codes",
]
