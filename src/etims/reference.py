"""Reference data from KRA: device setup, code lists, classifications, taxpayers..."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Generic, TypeVar

from ._payload import Record
from .codes import CodeClassId, TaxpayerStatus, TaxType

T = TypeVar("T")


@dataclass(frozen=True)
class SearchResult(Generic[T]):
    """Records returned by a lookup, plus the server time to sync from next time.

    KRA lookups take a "last request date" and return what changed after it.
    Store ``as_of`` and pass it as ``since`` on the next call to fetch only
    changes.
    """

    items: list[T]
    as_of: datetime | None

    def __iter__(self) -> Iterator[T]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> T:
        return self.items[index]


@dataclass(frozen=True, kw_only=True)
class DeviceInfo:
    """Result of device initialization: who the device belongs to and its key."""

    tin: str | None
    taxpayer_name: str | None
    business_activity: str | None
    branch_id: str | None
    branch_name: str | None
    branch_opened: date | None
    county: str | None
    sub_county: str | None
    tax_locality: str | None
    location: str | None
    is_head_office: bool | None
    manager_name: str | None
    manager_phone: str | None
    manager_email: str | None
    device_id: str | None
    sdc_id: str | None
    mrc_no: str | None
    #: The communication key every other request needs. Keep it secret.
    cmc_key: str | None = field(repr=False)

    @classmethod
    def from_api(cls, data: Mapping[str, object]) -> DeviceInfo:
        r = Record(data)
        return cls(
            tin=r.text("tin"),
            taxpayer_name=r.text("taxprNm"),
            business_activity=r.text("bsnsActv"),
            branch_id=r.text("bhfId"),
            branch_name=r.text("bhfNm"),
            branch_opened=r.day("bhfOpenDt"),
            county=r.text("prvncNm"),
            sub_county=r.text("dstrtNm"),
            tax_locality=r.text("sctrNm"),
            location=r.text("locDesc"),
            is_head_office=r.flag("hqYn"),
            manager_name=r.text("mgrNm"),
            manager_phone=r.text("mgrTelNo"),
            manager_email=r.text("mgrEmail"),
            device_id=r.text("dvcId"),
            # The spec's table says "sdicId"; its sample and the API say "sdcId".
            sdc_id=r.text("sdcId", "sdicId"),
            mrc_no=r.text("mrcNo"),
            cmc_key=r.text("cmcKey"),
        )


@dataclass(frozen=True, kw_only=True)
class Code:
    code: str
    name: str | None
    description: str | None
    sort_order: int | None
    active: bool | None
    user_defined: tuple[str | None, str | None, str | None]

    @classmethod
    def from_api(cls, r: Record) -> Code:
        return cls(
            code=r.text("cd") or "",
            name=r.text("cdNm"),
            description=r.text("cdDesc"),
            sort_order=r.integer("srtOrd"),
            active=r.flag("useYn"),
            user_defined=(r.text("userDfnCd1"), r.text("userDfnCd2"), r.text("userDfnCd3")),
        )


@dataclass(frozen=True, kw_only=True)
class CodeClass:
    """One code list, e.g. class "17" (packaging units) and its codes."""

    class_id: str
    name: str | None
    description: str | None
    active: bool | None
    user_defined_names: tuple[str | None, str | None, str | None]
    codes: list[Code]

    @classmethod
    def from_api(cls, r: Record) -> CodeClass:
        return cls(
            class_id=r.text("cdCls") or "",
            name=r.text("cdClsNm"),
            description=r.text("cdClsDesc"),
            active=r.flag("useYn"),
            user_defined_names=(r.text("userDfnNm1"), r.text("userDfnNm2"), r.text("userDfnNm3")),
            codes=[Code.from_api(c) for c in r.records("dtlList")],
        )

    def get(self, code: str) -> Code | None:
        return next((c for c in self.codes if c.code == code), None)


def find_code_class(
    classes: Sequence[CodeClass] | SearchResult[CodeClass], class_id: CodeClassId | str
) -> CodeClass | None:
    """Pick one code list (e.g. ``CodeClassId.PACKAGING_UNIT``) from ``get_codes()``."""
    wanted = class_id.value if isinstance(class_id, CodeClassId) else class_id
    return next((c for c in classes if c.class_id == wanted), None)


def tax_rates_from_codes(
    classes: Sequence[CodeClass] | SearchResult[CodeClass],
) -> dict[TaxType, Decimal]:
    """Read the current VAT rates KRA publishes in code class 04 (``userDfnCd1``).

    Pass the result to ``OscuClient(tax_rates=...)`` to stay in step with KRA.
    Tax types missing from the list are left out.
    """
    tax_class = find_code_class(classes, CodeClassId.TAX_TYPE)
    rates: dict[TaxType, Decimal] = {}
    if tax_class is None:
        return rates
    for code in tax_class.codes:
        try:
            tax_type = TaxType(code.code)
        except ValueError:
            continue
        raw = code.user_defined[0]
        if raw is not None:
            try:
                rates[tax_type] = Decimal(raw)
            except ArithmeticError:
                continue
    return rates


@dataclass(frozen=True, kw_only=True)
class ItemClass:
    """An item classification (UNSPSC-based) to use as ``item_class_code``."""

    code: str
    name: str | None
    level: int | None
    tax_type: TaxType | None
    major_target: bool | None
    active: bool | None

    @classmethod
    def from_api(cls, r: Record) -> ItemClass:
        return cls(
            code=r.text("itemClsCd") or "",
            name=r.text("itemClsNm"),
            level=r.integer("itemClsLvl"),
            tax_type=r.enum(TaxType, "taxTyCd"),
            major_target=r.flag("mjrTgYn"),
            active=r.flag("useYn"),
        )


@dataclass(frozen=True, kw_only=True)
class Taxpayer:
    """A taxpayer found by PIN lookup."""

    pin: str
    name: str | None
    status: TaxpayerStatus | None
    county: str | None
    sub_county: str | None
    tax_locality: str | None
    location: str | None

    @property
    def is_active(self) -> bool:
        return self.status is TaxpayerStatus.ACTIVE

    @classmethod
    def from_api(cls, r: Record) -> Taxpayer:
        return cls(
            pin=r.text("tin") or "",
            name=r.text("taxprNm"),
            status=r.enum(TaxpayerStatus, "taxprSttsCd"),
            county=r.text("prvncNm"),
            sub_county=r.text("dstrtNm"),
            tax_locality=r.text("sctrNm"),
            location=r.text("locDesc"),
        )


@dataclass(frozen=True, kw_only=True)
class Branch:
    """A branch of your business as registered with KRA."""

    tin: str | None
    branch_id: str
    name: str | None
    status_code: str | None
    county: str | None
    sub_county: str | None
    tax_locality: str | None
    location: str | None
    manager_name: str | None
    manager_phone: str | None
    manager_email: str | None
    is_head_office: bool | None

    @classmethod
    def from_api(cls, r: Record) -> Branch:
        return cls(
            tin=r.text("tin"),
            branch_id=r.text("bhfId") or "",
            name=r.text("bhfNm"),
            status_code=r.text("bhfSttsCd"),
            county=r.text("prvncNm"),
            sub_county=r.text("dstrtNm"),
            tax_locality=r.text("sctrNm"),
            location=r.text("locDesc"),
            manager_name=r.text("mgrNm"),
            manager_phone=r.text("mgrTelNo"),
            manager_email=r.text("mgrEmail"),
            is_head_office=r.flag("hqYn"),
        )


@dataclass(frozen=True, kw_only=True)
class Notice:
    """A notice KRA publishes to eTIMS users."""

    number: int | None
    title: str | None
    contents: str | None
    detail_url: str | None
    registered_by: str | None
    registered_at: datetime | None

    @classmethod
    def from_api(cls, r: Record) -> Notice:
        return cls(
            number=r.integer("noticeNo"),
            title=r.text("title"),
            contents=r.text("cont"),
            detail_url=r.text("dtlUrl"),
            registered_by=r.text("regrNm"),
            registered_at=r.timestamp("regDt"),
        )
