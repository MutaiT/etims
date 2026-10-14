"""Check a KRA eTIMS sandbox setup without creating any data.

    uv run python examples/check_sandbox.py

Reads OSCU_TIN, OSCU_BHF_ID and OSCU_CMC_KEY (or OSCU_DVC_SRL_NO to
initialize the device first) from the environment or .env.
"""

from __future__ import annotations

import sys

from _env import load_settings

from etims import (
    CodeClassId,
    DeviceError,
    EtimsError,
    OscuClient,
    find_code_class,
    tax_rates_from_codes,
)


def main() -> int:
    settings = load_settings()
    tin, bhf_id = settings.get("OSCU_TIN"), settings.get("OSCU_BHF_ID", "00")
    if not tin:
        print("Set OSCU_TIN (and OSCU_BHF_ID) in .env; see .env.example")
        return 1

    cmc_key = settings.get("OSCU_CMC_KEY")
    if not cmc_key:
        serial = settings.get("OSCU_DVC_SRL_NO")
        if not serial:
            print("Set OSCU_CMC_KEY, or OSCU_DVC_SRL_NO to initialize the device")
            return 1
        print(f"Initializing device for {tin} branch {bhf_id}...")
        try:
            device = OscuClient.initialize_device(tin, bhf_id, serial)
        except DeviceError as exc:
            print(f"  KRA refused the device: {exc}")
            print(
                "  The serial number is not approved as an OSCU device for this PIN and\n"
                "  branch. Check the service request on the eTIMS sandbox portal\n"
                "  (OSCU, not VSCU) and the exact serial KRA approved."
            )
            return 2
        print(f"  {device.taxpayer_name}, branch {device.branch_name} ({device.branch_id})")
        print(f"  Device {device.device_id}, control unit {device.sdc_id}")
        print("  Communication key received. Save it as OSCU_CMC_KEY in .env (keep it secret).")
        cmc_key = device.cmc_key
        assert cmc_key

    try:
        with OscuClient(tin, bhf_id, cmc_key) as client:
            codes = client.get_codes()
            classes = client.get_item_classes()
            branches = client.get_branches()
            notices = client.get_notices()
            me = client.get_taxpayer(tin)
    except EtimsError as exc:
        print(f"Request failed: {exc}")
        return 2

    print(f"Code lists: {len(codes)} (as of {codes.as_of})")
    units = find_code_class(codes, CodeClassId.PACKAGING_UNIT)
    print(f"  packaging units: {len(units.codes) if units else 0}")
    rates = {t.value: str(r) for t, r in tax_rates_from_codes(codes).items()}
    print(f"  tax rates: {rates}")
    print(f"Item classifications: {len(classes)}")
    print(f"Branches: {', '.join(f'{b.branch_id} {b.name}' for b in branches)}")
    print(f"Notices: {len(notices)}")
    if me:
        print(f"Your PIN: {me.name} ({'active' if me.is_active else 'inactive'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
