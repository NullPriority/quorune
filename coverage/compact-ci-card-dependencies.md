---
title: "Compact CI card dependencies"
status: "generated"
authoritative_source: "tests/fixtures/compact-ci-fixtures.json and platform/test-shards.json"
verified: "8fa53afed0806e2a375355b576891da96013a3e753ef6a906703b9c1e6caabd2"
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
| Fixture files | 278 |
| Cards | 3010 |
| Rulings | 6908 |
| Modules inspected | 562 |
| Static requirements | 1653 |
| Declared dynamic requirements | 46 |
| Unresolved dynamic sites | 0 |
| Missing cards | 0 |
| Missing deck dependencies | 0 |
| Fixture identity conflicts | 0 |

## Shard closure

| Shard | Modules | Status |
| --- | ---: | --- |
| casting-costs-mana | 58 | closed |
| combat-declarations | 22 | closed |
| compiler-cardprogram | 63 | closed |
| core-domain | 14 | closed |
| counter-continuous-effects | 46 | closed |
| deterministic-game-regressions | 6 | closed |
| events-replacement-zone | 41 | closed |
| functional-01 | 30 | closed |
| functional-02 | 70 | closed |
| functional-03 | 35 | closed |
| functional-04 | 35 | closed |
| functional-05 | 38 | closed |
| functional-06 | 53 | closed |
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
| server-replay-privacy | 18 | closed |
| state-actions-damage | 18 | closed |
| targets-choices-continuations | 32 | closed |
| triggers-turns-exact-decks | 20 | closed |
| windows-compat | 10 | closed |

The JSON companion contains canonical identities, fixture owners, source
provenance, unresolved dynamics, and exact missing dependencies.
