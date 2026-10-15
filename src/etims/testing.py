"""A local stand-in for KRA's OSCU API, for tests and demos.

Run it as a server::

    python -m etims.testing --port 8088

or use it in tests::

    with FakeKra() as kra:
        device = OscuClient.initialize_device(TIN, "00", "any-serial", base_url=kra.url)
        client = OscuClient(TIN, "00", device.cmc_key, base_url=kra.url)

It speaks the OSCU request and response format, signs sales, and keeps
registered items and invoices in memory (or in a JSON file). Where it helps
catch integration mistakes it is *stricter* than the specification: it
checks invoice numbering, item registration and totals. It is not KRA, so
passing here does not prove the real API accepts a request.

``fail_next()`` simulates outages: error codes, HTTP errors, slow answers
and dropped connections.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import secrets
import string
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ._util import EAT, is_valid_pin, kra_datetime
from .errors import RESULT_CODES

__all__ = ["FakeKra", "main"]

_CODE_LISTS: list[tuple[str, str, list[tuple[str, str, str | None]]]] = [
    (
        "04",
        "Taxation Type",
        [
            ("A", "A-Exempt", "0"),
            ("B", "B-16.00%", "16"),
            ("C", "C-0%", "0"),
            ("D", "D-Non-VAT", "0"),
            ("E", "E-8%", "8"),
        ],
    ),
    ("05", "Nation", [("KE", "KENYA", None), ("UG", "UGANDA", None), ("CN", "CHINA", None)]),
    (
        "07",
        "Payment Type",
        [
            ("01", "CASH", None),
            ("02", "CREDIT", None),
            ("03", "CASH/CREDIT", None),
            ("04", "BANK CHECK", None),
            ("05", "DEBIT&CREDIT CARD", None),
            ("06", "MOBILE MONEY", None),
            ("07", "OTHER", None),
        ],
    ),
    (
        "10",
        "Quantity Unit",
        [("U", "Pieces/item", None), ("KG", "Kilo-Gramme", None), ("L", "Litre", None)],
    ),
    ("17", "Packaging Unit", [("NT", "Net", None), ("CT", "Carton", None), ("BG", "Bag", None)]),
    (
        "32",
        "Credit Note Reason",
        [
            ("01", "Missing Quantity", None),
            ("02", "Missing data", None),
            ("03", "Damaged", None),
            ("04", "Wasted", None),
            ("05", "Raw Material Shortage", None),
            ("06", "Refund", None),
        ],
    ),
    ("33", "Currency", [("KES", "Kenyan shilling", None), ("USD", "US dollar", None)]),
]

# Sample classifications for demos; real ones come from KRA's /selectItemClsList.
_ITEM_CLASSES = [
    ("8111180000", "IT consulting services (sample)", "B"),
    ("4321150000", "Computers (sample)", "B"),
    ("5059690800", "Packaged food (sample)", "B"),
    ("5320000000", "Petroleum products (sample)", "E"),
    ("1010150000", "Live animals (sample)", "A"),
]

_ITEM_REQUIRED = [
    "itemCd",
    "itemClsCd",
    "itemTyCd",
    "itemNm",
    "orgnNatCd",
    "pkgUnitCd",
    "qtyUnitCd",
    "taxTyCd",
    "dftPrc",
    "isrcAplcbYn",
    "useYn",
    "regrId",
    "regrNm",
    "modrId",
    "modrNm",
]
_SALE_REQUIRED = [
    "trdInvcNo",
    "invcNo",
    "orgInvcNo",
    "rcptTyCd",
    "salesSttsCd",
    "cfmDt",
    "salesDt",
    "totItemCnt",
    "totTaxblAmt",
    "totTaxAmt",
    "totAmt",
    "prchrAcptcYn",
    "regrId",
    "regrNm",
    "modrId",
    "modrNm",
    "receipt",
    "itemList",
]
_LINE_REQUIRED = [
    "itemSeq",
    "itemCd",
    "itemNm",
    "pkgUnitCd",
    "pkg",
    "qtyUnitCd",
    "qty",
    "prc",
    "splyAmt",
    "dcRt",
    "dcAmt",
    "taxTyCd",
    "taxblAmt",
    "taxAmt",
    "totAmt",
]
_CENT = Decimal("0.01")


def _now() -> str:
    return kra_datetime(datetime.now(EAT))


def _envelope(code: str, message: str | None = None, data: Any = None) -> dict[str, Any]:
    known = RESULT_CODES.get(code)
    return {
        "resultCd": code,
        "resultMsg": message or (known.description if known else "Error"),
        "resultDt": _now(),
        "data": data,
    }


def _random(length: int) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _number(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("NaN")


def _differs(a: Decimal, b: Decimal) -> bool:
    """True unless both are numbers within a cent (NaN never compares equal)."""
    return not (a.is_finite() and b.is_finite()) or abs(a - b) > _CENT


@dataclass
class _Fault:
    result_code: str | None = None
    message: str | None = None
    http_status: int | None = None
    drop: bool = False
    lose_answer: bool = False
    delay: float = 0.0


@dataclass
class _Branch:
    taxpayer_name: str
    cmc_key: str
    items: dict[str, dict[str, Any]] = field(default_factory=dict)
    sales: dict[str, dict[str, Any]] = field(default_factory=dict)
    receipts_by_type: dict[str, int] = field(default_factory=dict)
    total_receipts: int = 0


class FakeKra:
    """An in-process OSCU API server. Use as a context manager or call ``start()``.

    ``approved_serials`` limits which device serials initialize (others get
    the 905 the live sandbox returns); by default any serial is accepted.
    ``state_file`` keeps items and invoices across restarts.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        *,
        approved_serials: set[str] | None = None,
        strict: bool = True,
        state_file: str | Path | None = None,
    ) -> None:
        self.strict = strict
        self._approved = approved_serials
        self._state_file = Path(state_file) if state_file else None
        self._lock = threading.Lock()
        self._branches: dict[str, _Branch] = {}
        self._faults: dict[str, list[_Fault]] = {}
        #: Every request received, as (path, body), for assertions in tests.
        self.requests: list[tuple[str, dict[str, Any] | None]] = []
        if self._state_file and self._state_file.exists():
            saved = json.loads(self._state_file.read_text())
            self._branches = {key: _Branch(**value) for key, value in saved.items()}
        self._server = ThreadingHTTPServer((host, port), self._handler_class())
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        address = self._server.server_address
        host = address[0] if isinstance(address[0], str) else address[0].decode()
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        return f"http://{host}:{address[1]}"

    def start(self) -> FakeKra:
        # A short poll interval lets stop() return quickly (the default is 0.5 s).
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread:
            self._thread.join(timeout=5)

    def __enter__(self) -> FakeKra:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def serve_forever(self) -> None:
        try:
            self._server.serve_forever()
        finally:
            self._server.server_close()

    def fail_next(
        self,
        path: str,
        *,
        result_code: str | None = None,
        message: str | None = None,
        http_status: int | None = None,
        drop: bool = False,
        lose_answer: bool = False,
        delay: float = 0.0,
        times: int = 1,
    ) -> None:
        """Make the next ``times`` requests to ``path`` fail.

        None of these record anything, except ``lose_answer``:

        * ``result_code`` answers with that KRA code (a rejection)
        * ``http_status`` answers with an HTTP error page
        * ``drop`` closes the connection without answering
        * ``lose_answer`` processes the request, then closes the connection
          without answering: KRA has it, the caller doesn't know
        * ``delay`` waits before answering (longer than the client's read timeout)
        """
        fault = _Fault(result_code, message, http_status, drop, lose_answer, delay)
        with self._lock:
            self._faults.setdefault(path, []).extend([fault] * times)

    def reset(self, *, keep_devices: bool = True) -> None:
        """Forget items, sales and pending faults, e.g. between tests.

        With ``keep_devices`` (the default) initialized devices and their
        communication keys survive, so clients keep working.
        """
        with self._lock:
            if keep_devices:
                for branch in self._branches.values():
                    branch.items.clear()
                    branch.sales.clear()
                    branch.receipts_by_type.clear()
                    branch.total_receipts = 0
            else:
                self._branches.clear()
            self._faults.clear()
            self.requests.clear()
            self._save_state()

    def sales(self, tin: str, bhf_id: str) -> dict[int, dict[str, Any]]:
        """Signed sales for a branch, by invoice number."""
        branch = self._branches.get(f"{tin}/{bhf_id}")
        return {int(k): v for k, v in branch.sales.items()} if branch else {}

    def items(self, tin: str, bhf_id: str) -> dict[str, dict[str, Any]]:
        branch = self._branches.get(f"{tin}/{bhf_id}")
        return dict(branch.items) if branch else {}

    # -- request handling ---------------------------------------------------

    def _take_fault(self, path: str) -> _Fault | None:
        with self._lock:
            queue = self._faults.get(path)
            return queue.pop(0) if queue else None

    def _save_state(self) -> None:
        if self._state_file:
            data = {key: asdict(branch) for key, branch in self._branches.items()}
            self._state_file.write_text(json.dumps(data, indent=1))

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                fault = fake._take_fault(self.path)
                if fault and fault.delay:
                    time.sleep(fault.delay)
                if fault and fault.drop:
                    self.close_connection = True
                    return
                if fault and fault.http_status:
                    self._send(
                        fault.http_status, b"<html><body>Server error</body></html>", "text/html"
                    )
                    return
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    body = None
                if fault and fault.result_code:
                    status, answer = 200, _envelope(fault.result_code, fault.message)
                else:
                    status, answer = fake._dispatch(self.path, self.headers, body)
                if fault and fault.lose_answer:
                    self.close_connection = True
                    return
                self._send(status, json.dumps(answer).encode(), "application/json")

            def _send(self, status: int, payload: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        return Handler

    def _dispatch(self, path: str, headers: Message, body: Any) -> tuple[int, dict[str, Any]]:
        self.requests.append((path, body if isinstance(body, dict) else None))
        if not isinstance(body, dict):
            return 200, _envelope("911")
        if path == "/selectInitOsdcInfo":
            return 200, self._initialize(body)
        handler = self._routes().get(path)
        if handler is None:
            return 404, {"error": f"No such endpoint: {path}"}
        tin, bhf_id, key = headers.get("tin"), headers.get("bhfId"), headers.get("cmcKey")
        if not (tin and bhf_id and key):
            return 200, _envelope("900")
        with self._lock:
            branch = self._branches.get(f"{tin}/{bhf_id}")
            if branch is None or not secrets.compare_digest(branch.cmc_key, key):
                return 200, _envelope("901")
            answer = handler(branch, body)
            self._save_state()
        return 200, answer

    def _routes(self) -> dict[str, Any]:
        def ok(branch: _Branch, body: dict[str, Any]) -> dict[str, Any]:
            return _envelope("000")

        def nothing(branch: _Branch, body: dict[str, Any]) -> dict[str, Any]:
            return _envelope("001")

        return {
            "/selectCodeList": self._codes,
            "/selectItemClsList": self._item_classes,
            "/selectCustomer": self._customer,
            "/selectBhfList": self._branch_list,
            "/selectNoticeList": nothing,
            "/saveBhfCustomer": ok,
            "/saveBhfUser": ok,
            "/saveBhfInsurance": ok,
            "/saveItem": self._save_item,
            "/selectItemList": self._item_list,
            "/selectImportItemList": nothing,
            "/updateImportItem": ok,
            "/saveTrnsSalesOsdc": self._save_sale,
            "/selectTrnsPurchaseSalesList": nothing,
            "/insertTrnsPurchase": ok,
            "/selectStockMoveList": nothing,
            "/insertStockIO": ok,
            "/saveStockMaster": ok,
        }

    def _initialize(self, body: dict[str, Any]) -> dict[str, Any]:
        tin, bhf_id, serial = body.get("tin"), body.get("bhfId"), body.get("dvcSrlNo")
        if not (isinstance(tin, str) and is_valid_pin(tin) and bhf_id and serial):
            return _envelope("910", "Request parameter error (tin, bhfId, dvcSrlNo)")
        if self._approved is not None and serial not in self._approved:
            return _envelope("905", "Only OSCU device can be verified.")
        key = hashlib.sha256(f"{tin}:{bhf_id}:{serial}".encode()).hexdigest()[:32]
        with self._lock:
            branch = self._branches.setdefault(
                f"{tin}/{bhf_id}", _Branch(taxpayer_name=f"Taxpayer {tin}", cmc_key=key)
            )
            branch.cmc_key = key
            self._save_state()
        info = {
            "tin": tin,
            "taxprNm": branch.taxpayer_name,
            "bsnsActv": "Trading",
            "bhfId": bhf_id,
            "bhfNm": "Headquarters" if bhf_id == "00" else f"Branch {bhf_id}",
            "bhfOpenDt": "20260101",
            "prvncNm": "NAIROBI CITY",
            "dstrtNm": "WESTLANDS",
            "sctrNm": "WESTLANDS",
            "locDesc": "Fake KRA",
            "hqYn": "Y" if bhf_id == "00" else "N",
            "mgrNm": "Manager",
            "mgrTelNo": "0700000000",
            "mgrEmail": "manager@example.com",
            "dvcId": "99999" + hashlib.sha256(serial.encode()).hexdigest()[:11].upper(),
            "sdcId": "KRACU0" + hashlib.sha256(tin.encode()).hexdigest()[:9].upper(),
            "mrcNo": "WIS" + hashlib.sha256(bhf_id.encode()).hexdigest()[:8].upper(),
            "cmcKey": key,
        }
        return _envelope("000", data={"info": info})

    def _codes(self, branch: _Branch, body: dict[str, Any]) -> dict[str, Any]:
        classes = [
            {
                "cdCls": cls,
                "cdClsNm": name,
                "cdClsDesc": None,
                "useYn": "Y",
                "userDfnNm1": "Tax Rate" if cls == "04" else None,
                "userDfnNm2": None,
                "userDfnNm3": None,
                "dtlList": [
                    {
                        "cd": cd,
                        "cdNm": cd_name,
                        "cdDesc": cd_name,
                        "useYn": "Y",
                        "srtOrd": order,
                        "userDfnCd1": extra,
                        "userDfnCd2": None,
                        "userDfnCd3": None,
                    }
                    for order, (cd, cd_name, extra) in enumerate(codes, start=1)
                ],
            }
            for cls, name, codes in _CODE_LISTS
        ]
        return _envelope("000", data={"clsList": classes})

    def _item_classes(self, branch: _Branch, body: dict[str, Any]) -> dict[str, Any]:
        classes = [
            {
                "itemClsCd": code,
                "itemClsNm": name,
                "itemClsLvl": 5,
                "taxTyCd": tax,
                "mjrTgYn": "N",
                "useYn": "Y",
            }
            for code, name, tax in _ITEM_CLASSES
        ]
        return _envelope("000", data={"itemClsList": classes})

    def _customer(self, branch: _Branch, body: dict[str, Any]) -> dict[str, Any]:
        pin = body.get("custmTin")
        if not (isinstance(pin, str) and is_valid_pin(pin)):
            return _envelope("910", "Request parameter error (custmTin)")
        customer = {
            "tin": pin,
            "taxprNm": f"Taxpayer {pin}",
            "taxprSttsCd": "A",
            "prvncNm": "NAIROBI CITY",
            "dstrtNm": "WESTLANDS",
            "sctrNm": "WESTLANDS",
            "locDesc": "Fake KRA",
        }
        return _envelope("000", data={"custList": [customer]})

    def _branch_list(self, branch: _Branch, body: dict[str, Any]) -> dict[str, Any]:
        bhf = {"tin": body.get("tin"), "bhfId": body.get("bhfId"), "bhfNm": "Headquarters"}
        return _envelope("000", data={"bhfList": [bhf | {"bhfSttsCd": "01", "hqYn": "Y"}]})

    def _save_item(self, branch: _Branch, body: dict[str, Any]) -> dict[str, Any]:
        missing = [key for key in _ITEM_REQUIRED if body.get(key) in (None, "")]
        if missing:
            return _envelope("910", f"Request parameter error ({', '.join(missing)})")
        branch.items[body["itemCd"]] = {
            k: v for k, v in body.items() if k not in {"tin", "bhfId", "cmcKey"}
        }
        return _envelope("000")

    def _item_list(self, branch: _Branch, body: dict[str, Any]) -> dict[str, Any]:
        if not branch.items:
            return _envelope("001")
        items = [item | {"regBhfId": "00", "rraModYn": "N"} for item in branch.items.values()]
        return _envelope("000", data={"itemList": items})

    def _save_sale(self, branch: _Branch, body: dict[str, Any]) -> dict[str, Any]:
        missing = [key for key in _SALE_REQUIRED if body.get(key) in (None, "")]
        if missing:
            return _envelope("910", f"Request parameter error ({', '.join(missing)})")
        number = body["invcNo"]
        if not isinstance(number, int) or number < 1:
            return _envelope("910", "Request parameter error (invcNo)")
        if str(number) in branch.sales:
            return _envelope("921")
        lines = body["itemList"]
        if not isinstance(lines, list) or not lines:
            return _envelope("910", "Request parameter error (itemList)")
        if body.get("totItemCnt") != len(lines):
            return _envelope("910", "totItemCnt does not match itemList")
        for line in lines:
            missing = [key for key in _LINE_REQUIRED if line.get(key) in (None, "")]
            if missing:
                return _envelope("910", f"Request parameter error (itemList: {', '.join(missing)})")
            if self.strict and line["itemCd"] not in branch.items:
                return _envelope("910", f"Item {line['itemCd']} is not registered")
        if self.strict:
            problem = self._check_sale(branch, body, lines)
            if problem:
                return _envelope("910", problem)
        receipt_type = str(body["rcptTyCd"])
        branch.receipts_by_type[receipt_type] = branch.receipts_by_type.get(receipt_type, 0) + 1
        branch.total_receipts += 1
        receipt = {
            "curRcptNo": branch.receipts_by_type[receipt_type],
            "totRcptNo": branch.total_receipts,
            "intrlData": _random(26),
            "rcptSign": _random(16),
            "sdcDateTime": _now(),
        }
        branch.sales[str(number)] = {"request": body, "receipt": receipt}
        return _envelope("000", data=receipt)

    @staticmethod
    def _check_sale(branch: _Branch, body: dict[str, Any], lines: list[dict[str, Any]]) -> str:
        expected = max((int(n) for n in branch.sales), default=0) + 1
        if body["invcNo"] != expected:
            return f"Invoice number {body['invcNo']} is out of sequence; expected {expected}"
        if body["rcptTyCd"] == "R" and str(body.get("orgInvcNo")) not in branch.sales:
            return f"Credit note refers to unknown invoice {body.get('orgInvcNo')}"
        for tax_type in "ABCDE":
            typed = [line for line in lines if line["taxTyCd"] == tax_type]
            taxable = sum((_number(line["taxblAmt"]) for line in typed), Decimal(0))
            tax = sum((_number(line["taxAmt"]) for line in typed), Decimal(0))
            if _differs(taxable, _number(body.get(f"taxblAmt{tax_type}"))):
                return f"taxblAmt{tax_type} does not match the lines"
            if _differs(tax, _number(body.get(f"taxAmt{tax_type}"))):
                return f"taxAmt{tax_type} does not match the lines"
        total = sum((_number(line["taxblAmt"]) for line in lines), Decimal(0))
        if _differs(total, _number(body["totTaxblAmt"])):
            return "totTaxblAmt does not match the lines"
        return ""


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run a local stand-in for KRA's OSCU API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument(
        "--approved-serial",
        action="append",
        dest="serials",
        help="only accept these device serials (repeatable; default: accept any)",
    )
    parser.add_argument("--state-file", help="keep items and invoices in this JSON file")
    parser.add_argument(
        "--lenient", action="store_true", help="skip numbering, registration and totals checks"
    )
    args = parser.parse_args(argv)
    fake = FakeKra(
        args.host,
        args.port,
        approved_serials=set(args.serials) if args.serials else None,
        strict=not args.lenient,
        state_file=args.state_file,
    )
    print(f"Fake KRA OSCU API listening on {fake.url} (Ctrl+C to stop)", flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        fake.serve_forever()


if __name__ == "__main__":
    main()
