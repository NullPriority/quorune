---
title: "Compact CI card dependencies"
status: "generated"
authoritative_source: "tests/fixtures/compact-ci-fixtures.json and platform/test-shards.json"
verified: "73fef8cbeef57b90667afadb842b3ffaf72f64fa6331c7fd8e6dffa7f7df05b1"
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
| Fixture files | 529 |
| Cards | 5658 |
| Rulings | 16859 |
| Modules inspected | 887 |
| Static requirements | 2516 |
| Declared dynamic requirements | 115 |
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
| counter-continuous-effects | 60 | closed |
| deterministic-game-regressions | 6 | closed |
| events-replacement-zone | 56 | closed |
| functional-01 | 62 | closed |
| functional-02 | 63 | closed |
| functional-03 | 80 | closed |
| functional-04 | 67 | closed |
| functional-05 | 56 | closed |
| functional-06 | 60 | closed |
| functional-07 | 62 | closed |
| functional-08 | 116 | closed |
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
| state-actions-damage | 18 | closed |
| targets-choices-continuations | 39 | closed |
| triggers-turns-exact-decks | 20 | closed |
| windows-compat | 18 | closed |

The JSON companion contains canonical identities, fixture owners, source
provenance, unresolved dynamics, and exact missing dependencies.
