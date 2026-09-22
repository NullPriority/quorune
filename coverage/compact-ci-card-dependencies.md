---
title: "Compact CI card dependencies"
status: "generated"
authoritative_source: "tests/fixtures/compact-ci-fixtures.json and platform/test-shards.json"
verified: "ee5411b3f20bd282f9ba5a1ccddfe25cdc2852f95ae15d194db275a971029280"
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
| Fixture files | 48 |
| Cards | 646 |
| Rulings | 1097 |
| Modules inspected | 348 |
| Static requirements | 1030 |
| Declared dynamic requirements | 12 |
| Unresolved dynamic sites | 0 |
| Missing cards | 0 |
| Missing deck dependencies | 0 |
| Fixture identity conflicts | 0 |

## Shard closure

| Shard | Modules | Status |
| --- | ---: | --- |
| casting-costs-mana | 57 | closed |
| combat-declarations | 22 | closed |
| compiler-cardprogram | 61 | closed |
| core-domain | 14 | closed |
| counter-continuous-effects | 36 | closed |
| deterministic-game-regressions | 6 | closed |
| events-replacement-zone | 40 | closed |
| functional-01 | 17 | closed |
| functional-02 | 57 | closed |
| functional-03 | 26 | closed |
| functional-04 | 24 | closed |
| functional-05 | 20 | closed |
| functional-06 | 27 | closed |
| functional-07 | 17 | closed |
| functional-08 | 19 | closed |
| functional-09 | 22 | closed |
| functional-10 | 21 | closed |
| functional-11 | 39 | closed |
| functional-12 | 27 | closed |
| generated-validation | 32 | closed |
| main-integration-smoke | 3 | closed |
| main-smoke | 6 | closed |
| merge-core | 9 | closed |
| multiplayer-commander | 9 | closed |
| nightly-property | 3 | closed |
| server-replay-privacy | 16 | closed |
| state-actions-damage | 17 | closed |
| targets-choices-continuations | 30 | closed |
| triggers-turns-exact-decks | 20 | closed |
| windows-compat | 10 | closed |

The JSON companion contains canonical identities, fixture owners, source
provenance, unresolved dynamics, and exact missing dependencies.
