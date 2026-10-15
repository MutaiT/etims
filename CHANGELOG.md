# Changelog

## 0.1.0a1 (2026-10-15)

First alpha. Built against KRA's OSCU specification v2.0; signing against the
live sandbox is not verified yet.

- `OscuClient` for every OSCU endpoint the specification defines: device
  initialization, code lists, item classifications, taxpayer lookup, branches,
  notices, branch customers/users/insurance, items, imported items, sales and
  credit notes, purchases, stock movements, stock in/out and stock levels.
- Typed request and response models that enforce KRA's field rules locally.
- VAT-inclusive tax calculation with `Decimal` and per-line rounding.
- Typed exceptions for every KRA result code, including 905 seen on the live sandbox.
- Retry policy that never re-sends a save that may have reached KRA.
- `SaleOutbox`: gap-free invoice numbering and in-order delivery through outages.
- `etims.testing.FakeKra`: a local stand-in for the OSCU API with fault injection,
  usable in tests or as a server (`python -m etims.testing`).
  `reset()` forgets items and sales between tests.
