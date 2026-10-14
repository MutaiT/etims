"""Read-only checks against the real KRA sandbox.

Run with ``uv run pytest -m live``. Needs OSCU_TIN and OSCU_BHF_ID plus either
OSCU_CMC_KEY or OSCU_DVC_SRL_NO, in the environment or in a .env file at the
project root (see .env.example). Nothing here creates data at KRA.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from etims import CodeClassId, OscuClient, TaxType, find_code_class, tax_rates_from_codes

pytestmark = pytest.mark.live


def _settings() -> dict[str, str]:
    settings = dict(os.environ)
    dotenv = Path(__file__).resolve().parent.parent / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                settings.setdefault(key.strip(), value.strip().strip("'\""))
    return settings


@pytest.fixture(scope="module")
def live() -> Iterator[OscuClient]:
    settings = _settings()
    tin, bhf_id = settings.get("OSCU_TIN"), settings.get("OSCU_BHF_ID")
    if not tin or not bhf_id:
        pytest.skip("Set OSCU_TIN and OSCU_BHF_ID (see .env.example)")
    cmc_key = settings.get("OSCU_CMC_KEY")
    if not cmc_key:
        serial = settings.get("OSCU_DVC_SRL_NO")
        if not serial:
            pytest.skip("Set OSCU_CMC_KEY or OSCU_DVC_SRL_NO")
        cmc_key = OscuClient.initialize_device(tin, bhf_id, serial).cmc_key
        assert cmc_key
    with OscuClient(tin, bhf_id, cmc_key) as client:
        yield client


def test_code_lists_include_tax_rates(live: OscuClient) -> None:
    codes = live.get_codes()
    assert find_code_class(codes, CodeClassId.TAX_TYPE) is not None
    assert tax_rates_from_codes(codes).get(TaxType.STANDARD) == 16


def test_item_classes(live: OscuClient) -> None:
    assert len(live.get_item_classes()) > 0


def test_own_branch_is_listed(live: OscuClient) -> None:
    assert any(branch.branch_id == live.bhf_id for branch in live.get_branches())


def test_own_pin_lookup(live: OscuClient) -> None:
    assert live.get_taxpayer(live.tin) is not None


def test_notices(live: OscuClient) -> None:
    live.get_notices()  # may well be empty; this checks the call is accepted
