from datetime import date, datetime
from decimal import Decimal

from etims import (
    EAT,
    Branch,
    CodeClass,
    CodeClassId,
    DeviceInfo,
    ItemClass,
    Notice,
    SearchResult,
    Taxpayer,
    TaxpayerStatus,
    TaxType,
    find_code_class,
    tax_rates_from_codes,
)
from etims._payload import Record

DEVICE = {
    "tin": "P051234567A",
    "taxprNm": "Example Traders Ltd",
    "bsnsActv": "Software",
    "bhfId": "00",
    "bhfNm": "Head Office",
    "bhfOpenDt": "20260101",
    "prvncNm": "NAIROBI CITY",
    "hqYn": "Y",
    "dvcId": "9999900000000001",
    "sdcId": "KRACU0100000001",
    "mrcNo": "WIS00000001",
    "cmcKey": "secret-communication-key",
}


def test_device_info() -> None:
    device = DeviceInfo.from_api(DEVICE)
    assert device.taxpayer_name == "Example Traders Ltd"
    assert device.branch_opened == date(2026, 1, 1)
    assert device.is_head_office is True
    assert device.sdc_id == "KRACU0100000001"
    assert device.cmc_key == "secret-communication-key"
    assert "secret-communication-key" not in repr(device)


def test_device_info_accepts_the_specs_sdicid_spelling() -> None:
    data = {k: v for k, v in DEVICE.items() if k != "sdcId"} | {"sdicId": "KRACU0100000009"}
    assert DeviceInfo.from_api(data).sdc_id == "KRACU0100000009"


CODES = [
    {
        "cdCls": "04",
        "cdClsNm": "Taxation Type",
        "useYn": "Y",
        "userDfnNm1": "Tax Rate",
        "dtlList": [
            {"cd": "A", "cdNm": "A-Exempt", "srtOrd": 1, "useYn": "Y", "userDfnCd1": "0"},
            {"cd": "B", "cdNm": "B-16.00%", "srtOrd": 2, "useYn": "Y", "userDfnCd1": "16"},
            {"cd": "E", "cdNm": "E-8%", "srtOrd": 5, "useYn": "Y", "userDfnCd1": "8"},
            {"cd": "Z", "cdNm": "Unknown", "srtOrd": 9, "useYn": "Y", "userDfnCd1": "5"},
            {"cd": "C", "cdNm": "C", "srtOrd": 3, "useYn": "Y", "userDfnCd1": "n/a"},
        ],
    },
    {"cdCls": "17", "cdClsNm": "Packaging Unit", "dtlList": [{"cd": "NT", "cdNm": "Net"}]},
]


def test_code_lists() -> None:
    classes = [CodeClass.from_api(Record(c)) for c in CODES]
    packaging = find_code_class(classes, CodeClassId.PACKAGING_UNIT)
    assert packaging is not None
    net = packaging.get("NT")
    assert net is not None
    assert net.name == "Net"
    assert packaging.get("XX") is None
    assert find_code_class(classes, "99") is None


def test_tax_rates_come_from_code_class_04() -> None:
    classes = SearchResult([CodeClass.from_api(Record(c)) for c in CODES], as_of=None)
    assert tax_rates_from_codes(classes) == {
        TaxType.EXEMPT: Decimal(0),
        TaxType.STANDARD: Decimal(16),
        TaxType.REDUCED: Decimal(8),
    }
    assert tax_rates_from_codes([]) == {}


def test_item_class_taxpayer_branch_and_notice() -> None:
    item_class = ItemClass.from_api(
        Record(
            {
                "itemClsCd": "5059690800",
                "itemClsNm": "Coffee",
                "itemClsLvl": 5,
                "taxTyCd": "B",
                "mjrTgYn": "N",
                "useYn": "Y",
            }
        )
    )
    assert item_class.tax_type is TaxType.STANDARD
    assert item_class.level == 5

    taxpayer = Taxpayer.from_api(
        Record({"tin": "A123456789Z", "taxprNm": "Acme", "taxprSttsCd": "A"})
    )
    assert taxpayer.status is TaxpayerStatus.ACTIVE
    assert taxpayer.is_active
    inactive = Taxpayer.from_api(Record({"tin": "A123456789Z", "taxprSttsCd": "D"}))
    assert not inactive.is_active

    branch = Branch.from_api(Record({"bhfId": "01", "bhfNm": "Mombasa", "hqYn": "N"}))
    assert (branch.branch_id, branch.is_head_office) == ("01", False)

    notice = Notice.from_api(
        Record({"noticeNo": 42, "title": "Maintenance", "regDt": "20261001080000"})
    )
    assert notice.number == 42
    assert notice.registered_at == datetime(2026, 10, 1, 8, 0, tzinfo=EAT)


def test_search_result_behaves_like_a_list() -> None:
    result = SearchResult(["a", "b"], as_of=None)
    assert len(result) == 2
    assert list(result) == ["a", "b"]
    assert result[1] == "b"


def test_records_ignore_malformed_values() -> None:
    r = Record({"n": "abc", "d": "x", "b": True, "flag": "maybe", "list": "nope"})
    assert r.integer("n") is None
    assert r.decimal("d") is None
    assert r.integer("b") is None
    assert r.flag("flag") is None
    assert r.records("list") == []
    assert r.enum(TaxType, "missing") is None
