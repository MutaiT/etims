"""KRA eTIMS environments."""

from __future__ import annotations

from enum import Enum


class Environment(str, Enum):
    """Which KRA eTIMS server to talk to."""

    SANDBOX = "sandbox"
    PRODUCTION = "production"

    @property
    def api_url(self) -> str:
        """OSCU API base URL, from section 1.2 of the specification."""
        return _API_URLS[self]

    @property
    def receipt_url(self) -> str:
        """Public receipt page that invoice QR codes point to."""
        return _RECEIPT_URLS[self]


_API_URLS = {
    Environment.SANDBOX: "https://etims-api-sbx.kra.go.ke/etims-api",
    Environment.PRODUCTION: "https://etims-api.kra.go.ke/etims-api",
}

# Not in the OSCU specification; this is the format KRA's receipt pages use.
_RECEIPT_URLS = {
    Environment.SANDBOX: "https://etims-sbx.kra.go.ke/common/link/etims/receipt/indexEtimsReceiptData",
    Environment.PRODUCTION: "https://etims.kra.go.ke/common/link/etims/receipt/indexEtimsReceiptData",
}
