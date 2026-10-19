---
title: "Compact CI card dependencies"
status: "generated"
authoritative_source: "tests/fixtures/compact-ci-fixtures.json and platform/test-shards.json"
verified: "c4429c338d3ce02dda9719d3aeeaa8ee42274c9f8983f282d69e6b17d2c995ad"
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
| Fixture files | 257 |
| Cards | 2837 |
| Rulings | 6362 |
| Modules inspected | 541 |
| Static requirements | 1605 |
| Declared dynamic requirements | 45 |
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
| functional-01 | 26 | closed |
| functional-02 | 64 | closed |
| functional-03 | 32 | closed |
| functional-04 | 35 | closed |
| functional-05 | 38 | closed |
| functional-06 | 52 | closed |
| functional-07 | 27 | closed |
| functional-08 | 36 | closed |
| functional-09 | 45 | closed |
| functional-10 | 34 | closed |
| functional-11 | 62 | closed |
| functional-12 | 58 | closed |
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
