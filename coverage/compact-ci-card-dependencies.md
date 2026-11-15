---
title: "Compact CI card dependencies"
status: "generated"
authoritative_source: "tests/fixtures/compact-ci-fixtures.json and platform/test-shards.json"
verified: "120363f978dc0e0ef02d52717c574d3da84029a2d59efcb0c3bdb44e5b9c9825"
audience: "maintainers and contributors"
maintenance: "generated"
---

# Compact CI card dependencies

This report measures whether every test module assigned to a compact-card
database shard has a statically discovered or explicitly declared card and
deck dependency that resolves through the canonical fixture manifest.

Overall closure: **closed**.

| Measure | Value |
| --- | ---: |
| Fixture files | 693 |
| Cards | 7185 |
| Rulings | 22500 |
| Modules inspected | 1027 |
| Static requirements | 3202 |
| Declared dynamic requirements | 187 |
| Unresolved dynamic sites | 0 |
| Missing cards | 0 |
| Missing deck dependencies | 0 |
| Fixture identity conflicts | 0 |

## Shard closure

| Shard | Modules | Status |
| --- | ---: | --- |
| casting-costs-mana | 74 | closed |
| combat-declarations | 25 | closed |
| compiler-cardprogram | 103 | closed |
| core-domain | 14 | closed |
| counter-continuous-effects | 64 | closed |
| deterministic-game-regressions | 6 | closed |
| events-replacement-zone | 60 | closed |
| functional-01 | 77 | closed |
| functional-02 | 72 | closed |
| functional-03 | 87 | closed |
| functional-04 | 84 | closed |
| functional-05 | 66 | closed |
| functional-06 | 75 | closed |
| functional-07 | 76 | closed |
| functional-08 | 130 | closed |
| functional-09 | 102 | closed |
| functional-10 | 68 | closed |
| functional-11 | 87 | closed |
| functional-12 | 69 | closed |
| generated-validation | 34 | closed |
| main-integration-smoke | 3 | closed |
| main-smoke | 6 | closed |
| merge-core | 9 | closed |
| multiplayer-commander | 17 | closed |
| nightly-property | 3 | closed |
| server-replay-privacy | 37 | closed |
| state-actions-damage | 22 | closed |
| targets-choices-continuations | 39 | closed |
| triggers-turns-exact-decks | 20 | closed |
| windows-compat | 18 | closed |

The JSON companion contains canonical identities, fixture owners, source
provenance, unresolved dynamics, and exact missing dependencies.
