---
title: "Compact CI card dependencies"
status: "generated"
authoritative_source: "tests/fixtures/compact-ci-fixtures.json and platform/test-shards.json"
verified: "13a3bab5e76f55f30363f1d84bb1291a251364237af74e56530ccefa2c15ea41"
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
| Fixture files | 1130 |
| Cards | 8546 |
| Rulings | 29123 |
| Modules inspected | 1467 |
| Static requirements | 6505 |
| Declared dynamic requirements | 5138 |
| Unresolved dynamic sites | 0 |
| Missing cards | 0 |
| Missing deck dependencies | 0 |
| Fixture identity conflicts | 0 |

## Shard closure

| Shard | Modules | Status |
| --- | ---: | --- |
| casting-costs-mana | 92 | closed |
| combat-declarations | 29 | closed |
| compiler-cardprogram | 149 | closed |
| core-domain | 14 | closed |
| counter-continuous-effects | 67 | closed |
| deterministic-game-regressions | 6 | closed |
| events-replacement-zone | 82 | closed |
| functional-01 | 96 | closed |
| functional-02 | 115 | closed |
| functional-03 | 179 | closed |
| functional-04 | 110 | closed |
| functional-05 | 94 | closed |
| functional-06 | 128 | closed |
| functional-07 | 121 | closed |
| functional-08 | 166 | closed |
| functional-09 | 122 | closed |
| functional-10 | 112 | closed |
| functional-11 | 98 | closed |
| functional-12 | 92 | closed |
| generated-validation | 34 | closed |
| main-integration-smoke | 3 | closed |
| main-smoke | 6 | closed |
| merge-core | 9 | closed |
| multiplayer-commander | 17 | closed |
| nightly-property | 3 | closed |
| server-replay-privacy | 65 | closed |
| state-actions-damage | 23 | closed |
| targets-choices-continuations | 81 | closed |
| triggers-turns-exact-decks | 21 | closed |
| windows-compat | 18 | closed |

The JSON companion contains canonical identities, fixture owners, source
provenance, unresolved dynamics, and exact missing dependencies.
