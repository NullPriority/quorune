---
title: "Compact CI card dependencies"
status: "generated"
authoritative_source: "tests/fixtures/compact-ci-fixtures.json and platform/test-shards.json"
verified: "b47889b83b151784fed944c87a654e25bbc867f48d777935275a15d8583bd363"
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
| Fixture files | 852 |
| Cards | 7883 |
| Rulings | 26746 |
| Modules inspected | 1187 |
| Static requirements | 3963 |
| Declared dynamic requirements | 1080 |
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
| functional-01 | 80 | closed |
| functional-02 | 91 | closed |
| functional-03 | 122 | closed |
| functional-04 | 97 | closed |
| functional-05 | 72 | closed |
| functional-06 | 112 | closed |
| functional-07 | 90 | closed |
| functional-08 | 138 | closed |
| functional-09 | 105 | closed |
| functional-10 | 89 | closed |
| functional-11 | 87 | closed |
| functional-12 | 70 | closed |
| generated-validation | 34 | closed |
| main-integration-smoke | 3 | closed |
| main-smoke | 6 | closed |
| merge-core | 9 | closed |
| multiplayer-commander | 17 | closed |
| nightly-property | 3 | closed |
| server-replay-privacy | 37 | closed |
| state-actions-damage | 23 | closed |
| targets-choices-continuations | 39 | closed |
| triggers-turns-exact-decks | 20 | closed |
| windows-compat | 18 | closed |

The JSON companion contains canonical identities, fixture owners, source
provenance, unresolved dynamics, and exact missing dependencies.
