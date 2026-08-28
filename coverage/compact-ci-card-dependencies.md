---
title: "Compact CI card dependencies"
status: "generated"
authoritative_source: "tests/fixtures/compact-ci-fixtures.json and platform/test-shards.json"
verified: "4d1812d57eaa7639e408f53c93e8e595625730ab4a8d0c5ed8a1bd081092186f"
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
| Fixture files | 37 |
| Cards | 459 |
| Rulings | 855 |
| Modules inspected | 317 |
| Static requirements | 847 |
| Declared dynamic requirements | 9 |
| Unresolved dynamic sites | 0 |
| Missing cards | 0 |
| Missing deck dependencies | 0 |
| Fixture identity conflicts | 0 |

## Shard closure

| Shard | Modules | Status |
| --- | ---: | --- |
| casting-costs-mana | 46 | closed |
| combat-declarations | 21 | closed |
| compiler-cardprogram | 51 | closed |
| core-domain | 14 | closed |
| counter-continuous-effects | 28 | closed |
| deterministic-game-regressions | 5 | closed |
| events-replacement-zone | 36 | closed |
| functional-01 | 13 | closed |
| functional-02 | 52 | closed |
| functional-03 | 21 | closed |
| functional-04 | 19 | closed |
| functional-05 | 19 | closed |
| functional-06 | 27 | closed |
| functional-07 | 16 | closed |
| functional-08 | 16 | closed |
| functional-09 | 19 | closed |
| functional-10 | 20 | closed |
| functional-11 | 38 | closed |
| functional-12 | 25 | closed |
| generated-validation | 32 | closed |
| main-integration-smoke | 3 | closed |
| main-smoke | 6 | closed |
| merge-core | 9 | closed |
| multiplayer-commander | 9 | closed |
| nightly-property | 3 | closed |
| server-replay-privacy | 14 | closed |
| state-actions-damage | 17 | closed |
| targets-choices-continuations | 26 | closed |
| triggers-turns-exact-decks | 19 | closed |
| windows-compat | 10 | closed |

The JSON companion contains canonical identities, fixture owners, source
provenance, unresolved dynamics, and exact missing dependencies.
