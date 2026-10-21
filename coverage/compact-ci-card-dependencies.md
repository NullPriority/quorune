---
title: "Compact CI card dependencies"
status: "generated"
authoritative_source: "tests/fixtures/compact-ci-fixtures.json and platform/test-shards.json"
verified: "e061fc748ea40505b7fae0b22db58442e11082965efa2dd76e420010702c0688"
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
| Fixture files | 296 |
| Cards | 3107 |
| Rulings | 7243 |
| Modules inspected | 580 |
| Static requirements | 1689 |
| Declared dynamic requirements | 46 |
| Unresolved dynamic sites | 0 |
| Missing cards | 0 |
| Missing deck dependencies | 0 |
| Fixture identity conflicts | 0 |

## Shard closure

| Shard | Modules | Status |
| --- | ---: | --- |
| casting-costs-mana | 69 | closed |
| combat-declarations | 24 | closed |
| compiler-cardprogram | 81 | closed |
| core-domain | 14 | closed |
| counter-continuous-effects | 52 | closed |
| deterministic-game-regressions | 6 | closed |
| events-replacement-zone | 45 | closed |
| functional-01 | 30 | closed |
| functional-02 | 83 | closed |
| functional-03 | 35 | closed |
| functional-04 | 35 | closed |
| functional-05 | 38 | closed |
| functional-06 | 56 | closed |
| functional-07 | 27 | closed |
| functional-08 | 39 | closed |
| functional-09 | 46 | closed |
| functional-10 | 37 | closed |
| functional-11 | 63 | closed |
| functional-12 | 59 | closed |
| generated-validation | 32 | closed |
| main-integration-smoke | 3 | closed |
| main-smoke | 6 | closed |
| merge-core | 9 | closed |
| multiplayer-commander | 9 | closed |
| nightly-property | 3 | closed |
| server-replay-privacy | 28 | closed |
| state-actions-damage | 18 | closed |
| targets-choices-continuations | 38 | closed |
| triggers-turns-exact-decks | 20 | closed |
| windows-compat | 10 | closed |

The JSON companion contains canonical identities, fixture owners, source
provenance, unresolved dynamics, and exact missing dependencies.
