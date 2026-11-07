---
title: "Compact CI card dependencies"
status: "generated"
authoritative_source: "tests/fixtures/compact-ci-fixtures.json and platform/test-shards.json"
verified: "d58fdc900d68e1ac74d8dc2739bd88e1b2df389d79c090c92cb25bfa55c70ccd"
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
| Fixture files | 535 |
| Cards | 5704 |
| Rulings | 16979 |
| Modules inspected | 890 |
| Static requirements | 2530 |
| Declared dynamic requirements | 177 |
| Unresolved dynamic sites | 0 |
| Missing cards | 0 |
| Missing deck dependencies | 0 |
| Fixture identity conflicts | 0 |

## Shard closure

| Shard | Modules | Status |
| --- | ---: | --- |
| casting-costs-mana | 71 | closed |
| combat-declarations | 25 | closed |
| compiler-cardprogram | 94 | closed |
| core-domain | 14 | closed |
| counter-continuous-effects | 61 | closed |
| deterministic-game-regressions | 6 | closed |
| events-replacement-zone | 56 | closed |
| functional-01 | 62 | closed |
| functional-02 | 64 | closed |
| functional-03 | 80 | closed |
| functional-04 | 68 | closed |
| functional-05 | 56 | closed |
| functional-06 | 60 | closed |
| functional-07 | 62 | closed |
| functional-08 | 117 | closed |
| functional-09 | 92 | closed |
| functional-10 | 60 | closed |
| functional-11 | 71 | closed |
| functional-12 | 64 | closed |
| generated-validation | 34 | closed |
| main-integration-smoke | 3 | closed |
| main-smoke | 6 | closed |
| merge-core | 9 | closed |
| multiplayer-commander | 17 | closed |
| nightly-property | 3 | closed |
| server-replay-privacy | 37 | closed |
| state-actions-damage | 19 | closed |
| targets-choices-continuations | 39 | closed |
| triggers-turns-exact-decks | 20 | closed |
| windows-compat | 18 | closed |

The JSON companion contains canonical identities, fixture owners, source
provenance, unresolved dynamics, and exact missing dependencies.
