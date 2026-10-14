from datetime import date
from decimal import Decimal

import pytest

from etims import (
    DEFAULT_OPERATOR,
    ImportedItem,
    ImportedItemUpdate,
    ImportItemStatus,
    InvalidDataError,
    Item,
    ProductType,
    RegisteredItem,
    TaxType,
    make_item_code,
)
from etims._payload import Record


def test_item_code_follows_the_specification_pattern() -> None:
    assert make_item_code(ProductType.FINISHED_PRODUCT, "NT", "BA", 12) == "KE2NTBA0000012"
    assert make_item_code(ProductType.SERVICE, "NT", "U", 1, origin_country="UG") == "UG3NTU0000001"


@pytest.mark.parametrize("sequence", [0, 10_000_000])
def test_item_code_sequence_bounds(sequence: int) -> None:
    with pytest.raises(InvalidDataError):
        make_item_code(ProductType.SERVICE, "NT", "U", sequence)


def test_item_code_length_limit() -> None:
    with pytest.raises(InvalidDataError, match="longer than 20"):
        make_item_code(ProductType.SERVICE, "LONGUNIT", "LONGUNIT", 1)


def _item(**overrides: object) -> Item:
    fields: dict[str, object] = {
        "item_code": "KE3NTU0000001",
        "item_class_code": "8111180000",
        "product_type": ProductType.SERVICE,
        "name": "Odoo integration",
        "packaging_unit": "NT",
        "quantity_unit": "U",
        "tax_type": TaxType.STANDARD,
        "default_price": Decimal("11600.00"),
    }
    fields.update(overrides)
    return Item(**fields)  # type: ignore[arg-type]


def test_item_body() -> None:
    body = _item(group_prices=(Decimal("10000"),)).to_api(DEFAULT_OPERATOR)
    assert body["itemCd"] == "KE3NTU0000001"
    assert body["itemTyCd"] == "3"
    assert body["orgnNatCd"] == "KE"
    assert body["taxTyCd"] == "B"
    assert body["dftPrc"] == 11600
    assert [body[f"grpPrcL{n}"] for n in range(1, 6)] == [10000, None, None, None, None]
    assert body["isrcAplcbYn"] == "N"
    assert body["useYn"] == "Y"
    assert body["regrId"] == body["modrId"] == "etims"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"group_prices": (1, 2, 3, 4, 5, 6)}, "at most 5 price levels"),
        ({"additional_info": "too long"}, "addInfo must be at most 7"),
        ({"default_price": Decimal("1.005")}, "dftPrc must have at most 2 decimal places"),
        ({"default_price": "abc"}, "dftPrc must be a number"),
        ({"item_class_code": None}, "itemClsCd is required"),
    ],
)
def test_invalid_items(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(InvalidDataError, match=message):
        _item(**overrides).to_api(DEFAULT_OPERATOR)


def test_registered_item_parsing() -> None:
    item = RegisteredItem.from_api(
        Record(
            {
                "tin": "P051234567A",
                "itemCd": "KE2NTU0000002",
                "itemClsCd": "5059690800",
                "itemTyCd": "2",
                "itemNm": "Router",
                "taxTyCd": "B",
                "regBhfId": "00",
                "dftPrc": 4999.5,
                "grpPrcL1": 0,
                "isrcAplcbYn": "N",
                "rraModYn": "N",
                "useYn": "Y",
            }
        )
    )
    assert item.product_type is ProductType.FINISHED_PRODUCT
    assert item.tax_type is TaxType.STANDARD
    assert item.default_price == Decimal("4999.5")
    assert item.group_prices == (Decimal(0), None, None, None, None)
    assert item.active is True
    assert item.kra_modified is False


def test_imported_item_parsing_accepts_both_status_spellings() -> None:
    for key in ("imptItemSttsCd", "imptItemsttsCd"):
        imported = ImportedItem.from_api(
            Record(
                {
                    "taskCd": "77001",
                    "dclDe": "-1   ",
                    "itemSeq": 1,
                    "hsCd": "8517620000",
                    key: "2",
                    "qty": 10,
                    "invcFcurAmt": 1250.5,
                    "invcFcurCd": "USD",
                }
            )
        )
        assert imported.status is ImportItemStatus.WAITING
        assert imported.declaration_date is None
        assert imported.quantity == Decimal(10)


def test_imported_item_update() -> None:
    imported = ImportedItem.from_api(
        Record({"taskCd": "77001", "dclDe": "20261001", "itemSeq": 3, "hsCd": "8517620000"})
    )
    update = ImportedItemUpdate.for_import(
        imported,
        item_class_code="4322260000",
        item_code="CN2NTU0000003",
        status=ImportItemStatus.APPROVED,
    )
    body = update.to_api(DEFAULT_OPERATOR)
    assert body["dclDe"] == "20261001"
    assert body["itemSeq"] == 3
    assert body["imptItemSttsCd"] == "3"
    assert body["modrId"] == "etims"
    assert "regrId" not in body


def test_imported_item_update_needs_the_declaration_details() -> None:
    incomplete = ImportedItem.from_api(Record({"taskCd": "77001"}))
    with pytest.raises(InvalidDataError, match="missing"):
        ImportedItemUpdate.for_import(
            incomplete,
            item_class_code="4322260000",
            item_code="CN2NTU0000003",
            status=ImportItemStatus.APPROVED,
        )


def test_declaration_date_type() -> None:
    update = ImportedItemUpdate(
        task_code="1",
        declaration_date=date(2026, 1, 2),
        item_seq=1,
        hs_code="1",
        item_class_code="1",
        item_code="1",
        status=ImportItemStatus.CANCELLED,
    )
    assert update.to_api(DEFAULT_OPERATOR)["dclDe"] == "20260102"
