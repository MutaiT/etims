"""Branch data you can store at KRA: customers, users and insurers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._payload import Body, Operator
from ._util import is_valid_pin
from .errors import InvalidDataError


@dataclass(frozen=True, kw_only=True)
class BranchCustomer:
    """A customer of your branch (``save_branch_customer``)."""

    customer_no: str
    customer_pin: str
    name: str
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    fax: str | None = None
    remark: str | None = None
    active: bool = True

    def to_api(self, operator: Operator) -> dict[str, Any]:
        if not is_valid_pin(self.customer_pin):
            raise InvalidDataError(
                f"BranchCustomer.custTin {self.customer_pin!r} is not a valid KRA PIN"
            )
        return (
            Body("BranchCustomer")
            .text("custNo", self.customer_no, 9, required=True)
            .text("custTin", self.customer_pin, 11, required=True)
            .text("custNm", self.name, 60, required=True)
            .text("adrs", self.address, 300)
            .text("telNo", self.phone, 20)
            .text("email", self.email, 50)
            .text("faxNo", self.fax, 20)
            .flag("useYn", self.active)
            .text("remark", self.remark, 1000)
            .operator(operator)
            .build()
        )


@dataclass(frozen=True, kw_only=True)
class BranchUser:
    """A user account of your branch (``save_branch_user``)."""

    user_id: str
    name: str
    password: str = field(repr=False)
    address: str | None = None
    contact: str | None = None
    authority: str | None = None
    remark: str | None = None
    active: bool = True

    def to_api(self, operator: Operator) -> dict[str, Any]:
        return (
            Body("BranchUser")
            .text("userId", self.user_id, 20, required=True)
            .text("userNm", self.name, 60, required=True)
            .text("pwd", self.password, 255, required=True)
            .text("adrs", self.address, 200)
            .text("cntc", self.contact, 20)
            .text("authCd", self.authority, 100)
            .text("remark", self.remark, 2000)
            .flag("useYn", self.active)
            .operator(operator)
            .build()
        )


@dataclass(frozen=True, kw_only=True)
class BranchInsurance:
    """An insurer your branch works with (``save_branch_insurance``; pharmacies only)."""

    code: str
    name: str
    #: Premium rate in percent.
    rate: int
    active: bool = True

    def to_api(self, operator: Operator) -> dict[str, Any]:
        return (
            Body("BranchInsurance")
            .text("isrccCd", self.code, 10, required=True)
            .text("isrccNm", self.name, 100, required=True)
            .integer("isrcRt", self.rate, required=True, minimum=0, maximum=100)
            .flag("useYn", self.active)
            .operator(operator)
            .build()
        )
