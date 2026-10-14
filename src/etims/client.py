"""The OSCU API client."""

from __future__ import annotations

import json
import logging
import os
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import TracebackType
from typing import Any, TypeVar

import requests
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError

from ._payload import Operator, Record
from ._util import EAT, is_valid_pin, kra_datetime, parse_kra_datetime
from .branches import BranchCustomer, BranchInsurance, BranchUser
from .codes import DEFAULT_TAX_RATES, TaxType
from .environment import Environment
from .errors import (
    ConfigurationError,
    ConnectionFailedError,
    InvalidDataError,
    KraError,
    ProtocolError,
    ResponseTimeoutError,
    TransportError,
    error_for,
)
from .items import ImportedItem, ImportedItemUpdate, Item, RegisteredItem
from .purchases import Purchase, PurchaseSale
from .reference import (
    Branch,
    CodeClass,
    DeviceInfo,
    ItemClass,
    Notice,
    SearchResult,
    Taxpayer,
)
from .sales import Sale, SaleReceipt
from .stock import StockIO, StockMaster, StockMovement

log = logging.getLogger("etims")

T = TypeVar("T")
Timeout = float | tuple[float, float]

#: Default ``since`` for lookups: early enough to return everything.
FULL_SYNC = datetime(2000, 1, 1, tzinfo=EAT)

#: Recorded at KRA as the registrant/modifier when you don't pass your own.
DEFAULT_OPERATOR = Operator(id="etims", name="etims Python SDK")

_SUCCESS = "000"
_NO_RESULTS = "001"


@dataclass(frozen=True)
class RetryPolicy:
    """When and how often to retry.

    Lookups are retried on any transient failure. Saves are retried only if
    the connection could not be made at all: once a save may have reached
    KRA, sending it again is the caller's decision (see ``SaleOutbox``).
    """

    attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0

    def delay(self, attempt: int) -> float:
        """Exponential backoff with full jitter, so clients don't retry in lockstep."""
        return random.uniform(0, min(self.max_delay, self.base_delay * 2**attempt))


def _never_connected(exc: requests.ConnectionError) -> bool:
    """True when the TCP/TLS connection was never established, so nothing was sent."""
    if isinstance(exc, requests.exceptions.SSLError):
        return True
    reason = exc.args[0] if exc.args else None
    reason = getattr(reason, "reason", reason)  # urllib3 wraps the cause in MaxRetryError
    return isinstance(reason, (NewConnectionError, ConnectTimeoutError))


def _should_retry(exc: Exception, *, read_only: bool) -> bool:
    if isinstance(exc, ConnectionFailedError):
        return True
    if not read_only:
        return False
    if isinstance(exc, TransportError):
        return True
    if isinstance(exc, ProtocolError):
        return exc.status_code is None or exc.status_code == 429 or exc.status_code >= 500
    if isinstance(exc, KraError):
        return exc.retryable
    return False


class _Transport:
    """POSTs JSON to the OSCU API and turns every failure into a typed error."""

    def __init__(
        self,
        base_url: str,
        session: requests.Session,
        timeout: Timeout,
        retry: RetryPolicy,
        sleep: Callable[[float], None],
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session
        self.timeout = timeout
        self.retry = retry
        self.sleep = sleep

    def post(
        self,
        path: str,
        body: Mapping[str, Any],
        *,
        headers: Mapping[str, str],
        read_only: bool,
    ) -> dict[str, Any]:
        """Send the request. Returns the response envelope for result 000 or 001."""
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        all_headers = {"Content-Type": "application/json", "Accept": "application/json", **headers}
        attempt = 0
        while True:
            try:
                return self._attempt(path, data, all_headers)
            except (TransportError, ProtocolError, KraError) as exc:
                attempt += 1
                if attempt >= self.retry.attempts or not _should_retry(exc, read_only=read_only):
                    raise
                delay = self.retry.delay(attempt - 1)
                log.warning("etims %s failed (%s); retry %d in %.1fs", path, exc, attempt, delay)
                self.sleep(delay)

    def _attempt(self, path: str, data: bytes, headers: Mapping[str, str]) -> dict[str, Any]:
        started = time.monotonic()
        try:
            response = self.session.post(
                self.base_url + path, data=data, headers=dict(headers), timeout=self.timeout
            )
        except requests.ConnectTimeout as exc:
            raise ConnectionFailedError(f"Could not connect to KRA for {path}: timed out") from exc
        except requests.ReadTimeout as exc:
            raise ResponseTimeoutError(f"KRA did not answer {path} in time") from exc
        except requests.ConnectionError as exc:
            if _never_connected(exc):
                raise ConnectionFailedError(f"Could not connect to KRA for {path}: {exc}") from exc
            raise TransportError(
                f"Connection to KRA failed during {path}: {exc}", may_have_been_processed=True
            ) from exc
        except requests.RequestException as exc:
            raise TransportError(
                f"Request to KRA failed for {path}: {exc}", may_have_been_processed=True
            ) from exc

        elapsed_ms = (time.monotonic() - started) * 1000
        if response.status_code != 200:
            raise ProtocolError(
                f"KRA returned HTTP {response.status_code} for {path}",
                status_code=response.status_code,
                body=response.text[:500],
                may_have_been_processed=response.status_code >= 500,
            )
        try:
            envelope = response.json()
        except ValueError as exc:
            raise ProtocolError(
                f"KRA's answer to {path} is not JSON",
                status_code=response.status_code,
                body=response.text[:500],
            ) from exc
        if not isinstance(envelope, dict) or "resultCd" not in envelope:
            raise ProtocolError(
                f"KRA's answer to {path} has no result code",
                status_code=response.status_code,
                body=response.text[:500],
            )

        code = str(envelope["resultCd"]).strip()
        envelope["resultCd"] = code
        log.info("etims %s -> %s (%.0f ms)", path, code, elapsed_ms)
        if code in (_SUCCESS, _NO_RESULTS):
            return envelope
        message = envelope.get("resultMsg")
        raise error_for(
            code,
            str(message).strip() if message else None,
            result_dt=parse_kra_datetime(str(envelope.get("resultDt") or "")),
            response=envelope,
        )


def _check_identity(tin: str, bhf_id: str) -> None:
    if not is_valid_pin(tin):
        raise ConfigurationError(f"{tin!r} is not a valid KRA PIN (e.g. P051234567A)")
    if len(bhf_id) != 2 or not bhf_id.isdigit():
        raise ConfigurationError(
            f"Branch ID {bhf_id!r} must be two digits: 00 for head office, 01.. for branches"
        )


class OscuClient:
    """Talks to KRA's eTIMS OSCU API on behalf of one PIN and branch.

    >>> client = OscuClient("P051234567A", "00", cmc_key, environment="sandbox")
    >>> receipt = client.save_sale(sale)

    Get ``cmc_key`` once with ``OscuClient.initialize_device()`` and store it
    as a secret. All methods raise subclasses of ``etims.EtimsError``.
    """

    def __init__(
        self,
        tin: str,
        bhf_id: str,
        cmc_key: str,
        *,
        environment: Environment | str = Environment.SANDBOX,
        base_url: str | None = None,
        operator: Operator = DEFAULT_OPERATOR,
        tax_rates: Mapping[TaxType, Decimal] | None = None,
        timeout: Timeout = (10.0, 30.0),
        retry: RetryPolicy | None = None,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        _check_identity(tin, bhf_id)
        if not cmc_key:
            raise ConfigurationError(
                "cmc_key is required; get it once with OscuClient.initialize_device()"
            )
        self.tin = tin
        self.bhf_id = bhf_id
        self._cmc_key = cmc_key
        self.environment = Environment(environment)
        self.operator = operator
        self.tax_rates: dict[TaxType, Decimal] = {**DEFAULT_TAX_RATES, **(tax_rates or {})}
        self._owns_session = session is None
        self._transport = _Transport(
            base_url or self.environment.api_url,
            session or requests.Session(),
            timeout,
            retry or RetryPolicy(),
            sleep,
        )

    def __repr__(self) -> str:
        return (
            f"OscuClient(tin={self.tin!r}, bhf_id={self.bhf_id!r}, "
            f"environment={self.environment.value!r})"
        )

    @classmethod
    def from_env(
        cls, *, prefix: str = "OSCU_", environ: Mapping[str, str] | None = None, **kwargs: Any
    ) -> OscuClient:
        """Build a client from OSCU_TIN, OSCU_BHF_ID and OSCU_CMC_KEY.

        Optional: OSCU_ENVIRONMENT (``sandbox`` or ``production``, default
        sandbox) and OSCU_BASE_URL. Other keyword arguments go to the constructor.
        """
        env = os.environ if environ is None else environ
        names = [f"{prefix}TIN", f"{prefix}BHF_ID", f"{prefix}CMC_KEY"]
        missing = [name for name in names if not env.get(name)]
        if missing:
            raise ConfigurationError(f"Missing environment variables: {', '.join(missing)}")
        kwargs.setdefault("environment", env.get(f"{prefix}ENVIRONMENT") or Environment.SANDBOX)
        kwargs.setdefault("base_url", env.get(f"{prefix}BASE_URL") or None)
        return cls(env[names[0]], env[names[1]], env[names[2]], **kwargs)

    @staticmethod
    def initialize_device(
        tin: str,
        bhf_id: str,
        device_serial: str,
        *,
        environment: Environment | str = Environment.SANDBOX,
        base_url: str | None = None,
        timeout: Timeout = (10.0, 30.0),
        session: requests.Session | None = None,
    ) -> DeviceInfo:
        """Verify an approved OSCU device with KRA and get its communication key.

        Do this once per branch and store ``DeviceInfo.cmc_key`` securely. KRA
        binds the key to this exact device serial number.
        """
        _check_identity(tin, bhf_id)
        if not device_serial or len(device_serial) > 100:
            raise ConfigurationError("device_serial is required (at most 100 characters)")
        env = Environment(environment)
        owned = session is None
        transport = _Transport(
            base_url or env.api_url,
            session or requests.Session(),
            timeout,
            RetryPolicy(),
            time.sleep,
        )
        try:
            envelope = transport.post(
                "/selectInitOsdcInfo",
                {"tin": tin, "bhfId": bhf_id, "dvcSrlNo": device_serial},
                headers={},
                read_only=True,
            )
        finally:
            if owned:
                transport.session.close()
        data = envelope.get("data")
        info = data.get("info") if isinstance(data, Mapping) else None
        if not isinstance(info, Mapping):
            raise ProtocolError("Device initialization returned no device information")
        device = DeviceInfo.from_api(info)
        if not device.cmc_key:
            raise ProtocolError("Device initialization returned no communication key")
        return device

    # -- transport helpers ------------------------------------------------------

    def _call(self, path: str, body: Mapping[str, Any], *, read_only: bool) -> dict[str, Any]:
        # The spec lists tin, bhfId and cmcKey as request fields but omits cmcKey from
        # its samples, while result code 900 ("There is no Header information") and
        # integrators' reports point to headers. Sending both satisfies either reading.
        auth = {"tin": self.tin, "bhfId": self.bhf_id, "cmcKey": self._cmc_key}
        return self._transport.post(path, {**auth, **body}, headers=auth, read_only=read_only)

    def _search(
        self,
        path: str,
        list_key: str,
        parse: Callable[[Record], T],
        since: datetime | None,
    ) -> SearchResult[T]:
        envelope = self._call(path, {"lastReqDt": kra_datetime(since or FULL_SYNC)}, read_only=True)
        as_of = parse_kra_datetime(str(envelope.get("resultDt") or ""))
        data = envelope.get("data")
        if envelope["resultCd"] == _NO_RESULTS or not isinstance(data, Mapping):
            return SearchResult([], as_of)
        return SearchResult([parse(r) for r in Record(data).records(list_key)], as_of)

    def _save(self, path: str, body: Mapping[str, Any]) -> None:
        self._call(path, body, read_only=False)

    # -- reference data ---------------------------------------------------------

    def get_codes(self, since: datetime | None = None) -> SearchResult[CodeClass]:
        """Standard code lists: units, countries, currencies, tax types..."""
        return self._search("/selectCodeList", "clsList", CodeClass.from_api, since)

    def get_item_classes(self, since: datetime | None = None) -> SearchResult[ItemClass]:
        """Item classifications to use as ``item_class_code``."""
        return self._search("/selectItemClsList", "itemClsList", ItemClass.from_api, since)

    def get_branches(self, since: datetime | None = None) -> SearchResult[Branch]:
        """Your business's branches as registered with KRA."""
        return self._search("/selectBhfList", "bhfList", Branch.from_api, since)

    def get_notices(self, since: datetime | None = None) -> SearchResult[Notice]:
        """Notices KRA publishes to eTIMS users."""
        return self._search("/selectNoticeList", "noticeList", Notice.from_api, since)

    def get_taxpayer(self, pin: str) -> Taxpayer | None:
        """Look up a customer's PIN. Returns None if KRA has no such taxpayer."""
        if not is_valid_pin(pin):
            raise InvalidDataError(f"{pin!r} is not a valid KRA PIN (e.g. P051234567A)")
        envelope = self._call("/selectCustomer", {"custmTin": pin}, read_only=True)
        data = envelope.get("data")
        if envelope["resultCd"] == _NO_RESULTS or not isinstance(data, Mapping):
            return None
        found = Record(data).records("custList")
        return Taxpayer.from_api(found[0]) if found else None

    # -- branch data ------------------------------------------------------------

    def save_branch_customer(
        self, customer: BranchCustomer, *, operator: Operator | None = None
    ) -> None:
        self._save("/saveBhfCustomer", customer.to_api(operator or self.operator))

    def save_branch_user(self, user: BranchUser, *, operator: Operator | None = None) -> None:
        self._save("/saveBhfUser", user.to_api(operator or self.operator))

    def save_branch_insurance(
        self, insurance: BranchInsurance, *, operator: Operator | None = None
    ) -> None:
        self._save("/saveBhfInsurance", insurance.to_api(operator or self.operator))

    # -- items ------------------------------------------------------------------

    def save_item(self, item: Item, *, operator: Operator | None = None) -> None:
        """Register or update an item. Items must be registered before they are sold."""
        self._save("/saveItem", item.to_api(operator or self.operator))

    def get_items(self, since: datetime | None = None) -> SearchResult[RegisteredItem]:
        """Items registered at KRA (useful to recover or reconcile your item list)."""
        return self._search("/selectItemList", "itemList", RegisteredItem.from_api, since)

    def get_imported_items(self, since: datetime | None = None) -> SearchResult[ImportedItem]:
        """Imports customs recorded under your PIN, to approve or reject."""
        return self._search("/selectImportItemList", "itemList", ImportedItem.from_api, since)

    def update_imported_item(
        self, update: ImportedItemUpdate, *, operator: Operator | None = None
    ) -> None:
        self._save("/updateImportItem", update.to_api(operator or self.operator))

    # -- sales ------------------------------------------------------------------

    def prepare_sale(self, sale: Sale, *, operator: Operator | None = None) -> dict[str, Any]:
        """Validate a sale and build its request body (without credentials).

        Useful for storing exactly what will be sent; ``submit_sale`` sends it.
        """
        return sale.to_api(operator or self.operator, self.tax_rates)

    def save_sale(self, sale: Sale, *, operator: Operator | None = None) -> SaleReceipt:
        """Sign a sale or credit note with KRA and return the receipt signature.

        If this raises an error with ``may_have_been_processed`` set, send the
        same sale again: a ``DuplicateError`` then means KRA already has it.
        """
        return self.submit_sale(self.prepare_sale(sale, operator=operator))

    def submit_sale(self, body: Mapping[str, Any]) -> SaleReceipt:
        """Send a body made by ``prepare_sale``."""
        invoice_no = body.get("invcNo")
        if not isinstance(invoice_no, int) or isinstance(invoice_no, bool):
            raise InvalidDataError("The sale body has no invcNo; build it with prepare_sale()")
        envelope = self._call("/saveTrnsSalesOsdc", body, read_only=False)
        data = envelope.get("data")
        if not isinstance(data, Mapping):
            raise ProtocolError(
                f"KRA accepted invoice {invoice_no} but returned no receipt data",
                may_have_been_processed=True,
            )
        return SaleReceipt.from_api(
            data,
            invoice_no=invoice_no,
            tin=self.tin,
            branch_id=self.bhf_id,
            environment=self.environment,
        )

    # -- purchases --------------------------------------------------------------

    def get_purchase_sales(self, since: datetime | None = None) -> SearchResult[PurchaseSale]:
        """Sales your suppliers declared with your PIN, to confirm as purchases."""
        return self._search(
            "/selectTrnsPurchaseSalesList", "saleList", PurchaseSale.from_api, since
        )

    def save_purchase(self, purchase: Purchase, *, operator: Operator | None = None) -> None:
        self._save(
            "/insertTrnsPurchase", purchase.to_api(operator or self.operator, self.tax_rates)
        )

    # -- stock ------------------------------------------------------------------

    def get_stock_movements(self, since: datetime | None = None) -> SearchResult[StockMovement]:
        """Stock moved to this branch (e.g. from head office)."""
        return self._search("/selectStockMoveList", "stockList", StockMovement.from_api, since)

    def save_stock_io(self, stock_io: StockIO, *, operator: Operator | None = None) -> None:
        self._save("/insertStockIO", stock_io.to_api(operator or self.operator, self.tax_rates))

    def save_stock_master(self, stock: StockMaster, *, operator: Operator | None = None) -> None:
        self._save("/saveStockMaster", stock.to_api(operator or self.operator))

    # -- lifecycle --------------------------------------------------------------

    def close(self) -> None:
        if self._owns_session:
            self._transport.session.close()

    def __enter__(self) -> OscuClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
