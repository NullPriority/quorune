---
title: "Compact CI card dependencies"
status: "generated"
authoritative_source: "tests/fixtures/compact-ci-fixtures.json and platform/test-shards.json"
verified: "4ee744334212d25c3fcfd385834790802c9bba80f246ef7a707c7e1ccf24700a"
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
| Fixture files | 142 |
| Cards | 1425 |
| Rulings | 2531 |
| Modules inspected | 425 |
| Static requirements | 1287 |
| Declared dynamic requirements | 25 |
| Unresolved dynamic sites | 0 |
| Missing cards | 0 |
| Missing deck dependencies | 0 |
| Fixture identity conflicts | 0 |

## Shard closure

| Shard | Modules | Status |
| --- | ---: | --- |
| casting-costs-mana | 57 | closed |
| combat-declarations | 22 | closed |
| compiler-cardprogram | 62 | closed |
| core-domain | 14 | closed |
| counter-continuous-effects | 36 | closed |
| deterministic-game-regressions | 6 | closed |
| events-replacement-zone | 40 | closed |
| functional-01 | 18 | closed |
| functional-02 | 61 | closed |
| functional-03 | 31 | closed |
| functional-04 | 31 | closed |
| functional-05 | 26 | closed |
| functional-06 | 29 | closed |
| functional-07 | 18 | closed |
| functional-08 | 27 | closed |
| functional-09 | 40 | closed |
| functional-10 | 21 | closed |
| functional-11 | 47 | closed |
| functional-12 | 44 | closed |
| generated-validation | 32 | closed |
| main-integration-smoke | 3 | closed |
| main-smoke | 6 | closed |
| merge-core | 9 | closed |
| multiplayer-commander | 9 | closed |
| nightly-property | 3 | closed |
| server-replay-privacy | 16 | closed |
| state-actions-damage | 17 | closed |
| targets-choices-continuations | 31 | closed |
| triggers-turns-exact-decks | 20 | closed |
| windows-compat | 10 | closed |

The JSON companion contains canonical identities, fixture owners, source
provenance, unresolved dynamics, and exact missing dependencies.
