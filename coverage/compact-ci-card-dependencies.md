---
title: "Compact CI card dependencies"
status: "generated"
authoritative_source: "tests/fixtures/compact-ci-fixtures.json and platform/test-shards.json"
verified: "78301e432ef3dc27574eb2ffc98e5518d14492b3cee7b9048b7a8723c86762c0"
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
| Fixture files | 457 |
| Cards | 5121 |
| Rulings | 14909 |
| Modules inspected | 797 |
| Static requirements | 2236 |
| Declared dynamic requirements | 84 |
| Unresolved dynamic sites | 0 |
| Missing cards | 0 |
| Missing deck dependencies | 0 |
| Fixture identity conflicts | 0 |

## Shard closure

| Shard | Modules | Status |
| --- | ---: | --- |
| casting-costs-mana | 70 | closed |
| combat-declarations | 24 | closed |
| compiler-cardprogram | 90 | closed |
| core-domain | 14 | closed |
| counter-continuous-effects | 60 | closed |
| deterministic-game-regressions | 6 | closed |
| events-replacement-zone | 54 | closed |
| functional-01 | 61 | closed |
| functional-02 | 59 | closed |
| functional-03 | 64 | closed |
| functional-04 | 60 | closed |
| functional-05 | 56 | closed |
| functional-06 | 59 | closed |
| functional-07 | 62 | closed |
| functional-08 | 65 | closed |
| functional-09 | 92 | closed |
| functional-10 | 56 | closed |
| functional-11 | 70 | closed |
| functional-12 | 59 | closed |
| generated-validation | 34 | closed |
| main-integration-smoke | 3 | closed |
| main-smoke | 6 | closed |
| merge-core | 9 | closed |
| multiplayer-commander | 17 | closed |
| nightly-property | 3 | closed |
| server-replay-privacy | 37 | closed |
| state-actions-damage | 18 | closed |
| targets-choices-continuations | 39 | closed |
| triggers-turns-exact-decks | 20 | closed |
| windows-compat | 18 | closed |

The JSON companion contains canonical identities, fixture owners, source
provenance, unresolved dynamics, and exact missing dependencies.
