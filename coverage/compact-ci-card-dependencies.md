---
title: "Compact CI card dependencies"
status: "generated"
authoritative_source: "tests/fixtures/compact-ci-fixtures.json and platform/test-shards.json"
verified: "f3733c36a6c0090bdafc0fa7206d751a7b6a850da74ed191e383fd330b4425ef"
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
| Fixture files | 273 |
| Cards | 2990 |
| Rulings | 6813 |
| Modules inspected | 557 |
| Static requirements | 1643 |
| Declared dynamic requirements | 46 |
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
| counter-continuous-effects | 46 | closed |
| deterministic-game-regressions | 6 | closed |
| events-replacement-zone | 40 | closed |
| functional-01 | 30 | closed |
| functional-02 | 67 | closed |
| functional-03 | 34 | closed |
| functional-04 | 35 | closed |
| functional-05 | 38 | closed |
| functional-06 | 52 | closed |
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
