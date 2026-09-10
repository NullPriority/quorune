---
title: "Compact CI card dependencies"
status: "generated"
authoritative_source: "tests/fixtures/compact-ci-fixtures.json and platform/test-shards.json"
verified: "e6bcb3f777c12ae0da2b0d3c7b2b4729f2d2232eab89014cf09bcdad963b7731"
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
| Fixture files | 46 |
| Cards | 618 |
| Rulings | 1097 |
| Modules inspected | 339 |
| Static requirements | 946 |
| Declared dynamic requirements | 12 |
| Unresolved dynamic sites | 0 |
| Missing cards | 0 |
| Missing deck dependencies | 0 |
| Fixture identity conflicts | 0 |

## Shard closure

| Shard | Modules | Status |
| --- | ---: | --- |
| casting-costs-mana | 53 | closed |
| combat-declarations | 21 | closed |
| compiler-cardprogram | 58 | closed |
| core-domain | 14 | closed |
| counter-continuous-effects | 35 | closed |
| deterministic-game-regressions | 6 | closed |
| events-replacement-zone | 40 | closed |
| functional-01 | 17 | closed |
| functional-02 | 56 | closed |
| functional-03 | 24 | closed |
| functional-04 | 23 | closed |
| functional-05 | 20 | closed |
| functional-06 | 27 | closed |
| functional-07 | 16 | closed |
| functional-08 | 19 | closed |
| functional-09 | 19 | closed |
| functional-10 | 21 | closed |
| functional-11 | 39 | closed |
| functional-12 | 26 | closed |
| generated-validation | 32 | closed |
| main-integration-smoke | 3 | closed |
| main-smoke | 6 | closed |
| merge-core | 9 | closed |
| multiplayer-commander | 9 | closed |
| nightly-property | 3 | closed |
| server-replay-privacy | 16 | closed |
| state-actions-damage | 17 | closed |
| targets-choices-continuations | 29 | closed |
| triggers-turns-exact-decks | 20 | closed |
| windows-compat | 10 | closed |

The JSON companion contains canonical identities, fixture owners, source
provenance, unresolved dynamics, and exact missing dependencies.
