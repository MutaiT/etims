"""A local outbox that delivers sales to KRA in order, even through outages.

eTIMS needs invoice numbers that increase by one with no gaps, and a sale
whose response was lost must not be signed twice. The outbox handles both:

* ``enqueue()`` gives a sale the next invoice number and stores the exact
  request body, in one database transaction. A sale that fails validation
  never consumes a number.
* ``flush()`` sends stored sales in invoice-number order and records each
  outcome. It stops at the first problem, so invoices never arrive out of
  order, and the next ``flush()`` resumes from there.
* If KRA did not answer, the sale is marked *uncertain* and re-sent
  unchanged next time. A ``DuplicateError`` on that resend proves KRA
  already recorded it.

Uses SQLite from the standard library. Run one flusher per database at a
time (e.g. a single scheduled job).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import Any

from ._payload import Operator
from ._util import now_eat
from .client import OscuClient
from .errors import (
    DuplicateError,
    InvalidDataError,
    KraError,
    ProtocolError,
    TransportError,
)
from .sales import Sale, SaleReceipt

_SCHEMA = """
CREATE TABLE IF NOT EXISTS etims_sequence (
    tin TEXT NOT NULL,
    bhf_id TEXT NOT NULL,
    last_invoice_no INTEGER NOT NULL,
    PRIMARY KEY (tin, bhf_id)
);
CREATE TABLE IF NOT EXISTS etims_outbox (
    tin TEXT NOT NULL,
    bhf_id TEXT NOT NULL,
    invoice_no INTEGER NOT NULL,
    trader_invoice_no TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    receipt TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tin, bhf_id, invoice_no)
);
"""


class OutboxStatus(str, Enum):
    PENDING = "pending"
    """Waiting to be sent."""
    UNCERTAIN = "uncertain"
    """Sent, but KRA did not answer. It is re-sent unchanged on the next flush."""
    SENT = "sent"
    """Signed by KRA; the receipt is stored."""
    REJECTED = "rejected"
    """KRA refused it. Correct it with ``requeue()``, which keeps its invoice number."""
    DUPLICATE = "duplicate"
    """KRA already recorded it, but the answer with its receipt was lost."""


@dataclass(frozen=True)
class OutboxEntry:
    invoice_no: int
    trader_invoice_no: str
    status: OutboxStatus
    attempts: int
    last_error: str | None
    receipt: SaleReceipt | None
    created_at: datetime
    updated_at: datetime


@dataclass
class FlushReport:
    """What one ``flush()`` did, by invoice number."""

    sent: list[int] = field(default_factory=list)
    rejected: list[int] = field(default_factory=list)
    uncertain: list[int] = field(default_factory=list)
    duplicates: list[int] = field(default_factory=list)
    #: Why the flush stopped before the end of the queue, if it did.
    stopped_reason: str | None = None


class SaleOutbox:
    """Durable, ordered delivery of sales for one PIN and branch.

    >>> with SaleOutbox("etims.sqlite3", client) as outbox:
    ...     invoice_no = outbox.enqueue(sale)
    ...     report = outbox.flush()

    ``start_after`` sets the last invoice number already used with KRA when
    the outbox is first created (e.g. when moving from another system).
    """

    def __init__(
        self, path: str | Path, client: OscuClient, *, start_after: int | None = None
    ) -> None:
        self.client = client
        self._key = (client.tin, client.bhf_id)
        self._lock = threading.RLock()  # guards the database connection, briefly
        self._flush_lock = threading.Lock()  # one flush at a time
        self._db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        if start_after is not None:
            if start_after < 0:
                raise InvalidDataError("start_after must be 0 or more")
            with self._transaction() as db:
                db.execute(
                    "INSERT OR IGNORE INTO etims_sequence (tin, bhf_id, last_invoice_no) "
                    "VALUES (?, ?, ?)",
                    (*self._key, start_after),
                )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    @property
    def last_invoice_no(self) -> int:
        """The last invoice number handed out (0 if none yet)."""
        with self._lock:
            row = self._db.execute(
                "SELECT last_invoice_no FROM etims_sequence WHERE tin = ? AND bhf_id = ?",
                self._key,
            ).fetchone()
        return int(row[0]) if row else 0

    def enqueue(self, sale: Sale, *, operator: Operator | None = None) -> int:
        """Assign the next invoice number to ``sale`` and store it for sending.

        Returns the invoice number. Leave ``sale.invoice_no`` unset.
        """
        if sale.invoice_no is not None:
            raise InvalidDataError(
                "Leave Sale.invoice_no unset: the outbox allocates invoice numbers"
            )
        with self._transaction() as db:
            row = db.execute(
                "SELECT last_invoice_no FROM etims_sequence WHERE tin = ? AND bhf_id = ?",
                self._key,
            ).fetchone()
            invoice_no = (int(row[0]) if row else 0) + 1
            # Validation errors raise here, inside the transaction, so a bad sale
            # never uses up an invoice number.
            body = self.client.prepare_sale(replace(sale, invoice_no=invoice_no), operator=operator)
            now = now_eat().isoformat()
            db.execute(
                "INSERT INTO etims_sequence (tin, bhf_id, last_invoice_no) VALUES (?, ?, ?) "
                "ON CONFLICT (tin, bhf_id) DO UPDATE "
                "SET last_invoice_no = excluded.last_invoice_no",
                (*self._key, invoice_no),
            )
            db.execute(
                "INSERT INTO etims_outbox (tin, bhf_id, invoice_no, trader_invoice_no, payload, "
                "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    *self._key,
                    invoice_no,
                    sale.trader_invoice_no,
                    json.dumps(body),
                    OutboxStatus.PENDING.value,
                    now,
                    now,
                ),
            )
        return invoice_no

    def flush(self, *, limit: int | None = None) -> FlushReport:
        """Send pending and uncertain sales in order, stopping at the first problem."""
        report = FlushReport()
        # KRA calls happen outside the database lock, so enqueue() is never
        # blocked waiting on the network.
        with self._flush_lock:
            with self._lock:
                rows = self._db.execute(
                    "SELECT invoice_no, payload, status FROM etims_outbox "
                    "WHERE tin = ? AND bhf_id = ? AND status IN (?, ?) ORDER BY invoice_no"
                    + (" LIMIT ?" if limit is not None else ""),
                    (
                        *self._key,
                        OutboxStatus.PENDING.value,
                        OutboxStatus.UNCERTAIN.value,
                        *([limit] if limit is not None else []),
                    ),
                ).fetchall()

            for invoice_no, payload, status in rows:
                was_uncertain = status == OutboxStatus.UNCERTAIN.value
                try:
                    receipt = self.client.submit_sale(json.loads(payload))
                except DuplicateError as exc:
                    if was_uncertain:
                        # KRA recorded an earlier attempt whose answer never arrived.
                        self._record(invoice_no, OutboxStatus.DUPLICATE, error=exc)
                        report.duplicates.append(invoice_no)
                        continue
                    self._record(invoice_no, OutboxStatus.REJECTED, error=exc)
                    report.rejected.append(invoice_no)
                    report.stopped_reason = f"Invoice {invoice_no} rejected: {exc}"
                    break
                except KraError as exc:
                    if exc.retryable:
                        # A server-side failure on a save (e.g. 999) does not prove the
                        # sale was dropped, so resend it as uncertain: a duplicate on the
                        # resend then means KRA had recorded it.
                        self._record(invoice_no, OutboxStatus.UNCERTAIN, error=exc)
                        report.uncertain.append(invoice_no)
                        report.stopped_reason = f"KRA failed at invoice {invoice_no}: {exc}"
                    else:
                        self._record(invoice_no, OutboxStatus.REJECTED, error=exc)
                        report.rejected.append(invoice_no)
                        report.stopped_reason = f"Invoice {invoice_no} rejected: {exc}"
                    break
                except (TransportError, ProtocolError) as exc:
                    if exc.may_have_been_processed:
                        self._record(invoice_no, OutboxStatus.UNCERTAIN, error=exc)
                        report.uncertain.append(invoice_no)
                        report.stopped_reason = (
                            f"No answer from KRA for invoice {invoice_no}; it will be re-sent"
                        )
                    else:
                        self._record(invoice_no, OutboxStatus(status), error=exc)
                        report.stopped_reason = f"KRA unreachable: {exc}"
                    break
                else:
                    self._record(invoice_no, OutboxStatus.SENT, receipt=receipt)
                    report.sent.append(invoice_no)
        return report

    def requeue(self, invoice_no: int, sale: Sale, *, operator: Operator | None = None) -> None:
        """Replace a rejected sale with corrected data, keeping its invoice number."""
        entry = self.get(invoice_no)
        if entry is None:
            raise InvalidDataError(f"Invoice {invoice_no} is not in the outbox")
        if entry.status is not OutboxStatus.REJECTED:
            raise InvalidDataError(
                f"Only rejected invoices can be requeued; "
                f"invoice {invoice_no} is {entry.status.value}"
            )
        body = self.client.prepare_sale(replace(sale, invoice_no=invoice_no), operator=operator)
        with self._transaction() as db:
            db.execute(
                "UPDATE etims_outbox SET payload = ?, trader_invoice_no = ?, status = ?, "
                "last_error = NULL, updated_at = ? WHERE tin = ? AND bhf_id = ? AND invoice_no = ?",
                (
                    json.dumps(body),
                    sale.trader_invoice_no,
                    OutboxStatus.PENDING.value,
                    now_eat().isoformat(),
                    *self._key,
                    invoice_no,
                ),
            )

    def get(self, invoice_no: int) -> OutboxEntry | None:
        with self._lock:
            row = self._db.execute(
                "SELECT invoice_no, trader_invoice_no, status, attempts, last_error, receipt, "
                "created_at, updated_at FROM etims_outbox "
                "WHERE tin = ? AND bhf_id = ? AND invoice_no = ?",
                (*self._key, invoice_no),
            ).fetchone()
        return self._entry(row) if row else None

    def entries(self, status: OutboxStatus | None = None) -> list[OutboxEntry]:
        query = (
            "SELECT invoice_no, trader_invoice_no, status, attempts, last_error, receipt, "
            "created_at, updated_at FROM etims_outbox WHERE tin = ? AND bhf_id = ?"
        )
        params: list[Any] = [*self._key]
        if status is not None:
            query += " AND status = ?"
            params.append(status.value)
        with self._lock:
            rows = self._db.execute(query + " ORDER BY invoice_no", params).fetchall()
        return [self._entry(row) for row in rows]

    def _record(
        self,
        invoice_no: int,
        status: OutboxStatus,
        *,
        error: Exception | None = None,
        receipt: SaleReceipt | None = None,
    ) -> None:
        with self._transaction() as db:
            db.execute(
                "UPDATE etims_outbox SET status = ?, attempts = attempts + 1, last_error = ?, "
                "receipt = COALESCE(?, receipt), updated_at = ? "
                "WHERE tin = ? AND bhf_id = ? AND invoice_no = ?",
                (
                    status.value,
                    str(error) if error else None,
                    json.dumps(dict(receipt.raw)) if receipt else None,
                    now_eat().isoformat(),
                    *self._key,
                    invoice_no,
                ),
            )

    def _entry(self, row: tuple[Any, ...]) -> OutboxEntry:
        invoice_no, trader_no, status, attempts, last_error, receipt, created, updated = row
        return OutboxEntry(
            invoice_no=int(invoice_no),
            trader_invoice_no=str(trader_no),
            status=OutboxStatus(status),
            attempts=int(attempts),
            last_error=last_error,
            receipt=(
                SaleReceipt.from_api(
                    json.loads(receipt),
                    invoice_no=int(invoice_no),
                    tin=self.client.tin,
                    branch_id=self.client.bhf_id,
                    environment=self.client.environment,
                )
                if receipt
                else None
            ),
            created_at=datetime.fromisoformat(created),
            updated_at=datetime.fromisoformat(updated),
        )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> SaleOutbox:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
