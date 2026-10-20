---
title: "Compact CI card dependencies"
status: "generated"
authoritative_source: "tests/fixtures/compact-ci-fixtures.json and platform/test-shards.json"
verified: "68d0eaf191c48afe87d0a85b4857d7790a96a9659a775e7403cb484cb1256892"
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
| Fixture files | 281 |
| Cards | 3030 |
| Rulings | 6900 |
| Modules inspected | 565 |
| Static requirements | 1659 |
| Declared dynamic requirements | 46 |
| Unresolved dynamic sites | 0 |
| Missing cards | 0 |
| Missing deck dependencies | 0 |
| Fixture identity conflicts | 0 |

## Shard closure

| Shard | Modules | Status |
| --- | ---: | --- |
| casting-costs-mana | 61 | closed |
| combat-declarations | 23 | closed |
| compiler-cardprogram | 67 | closed |
| core-domain | 14 | closed |
| counter-continuous-effects | 47 | closed |
| deterministic-game-regressions | 6 | closed |
| events-replacement-zone | 40 | closed |
| functional-01 | 30 | closed |
| functional-02 | 71 | closed |
| functional-03 | 34 | closed |
| functional-04 | 35 | closed |
| functional-05 | 38 | closed |
| functional-06 | 56 | closed |
| functional-07 | 27 | closed |
| functional-08 | 38 | closed |
| functional-09 | 46 | closed |
| functional-10 | 36 | closed |
| functional-11 | 63 | closed |
| functional-12 | 59 | closed |
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
