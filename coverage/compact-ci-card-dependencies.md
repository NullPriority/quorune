---
title: "Compact CI card dependencies"
status: "generated"
authoritative_source: "tests/fixtures/compact-ci-fixtures.json and platform/test-shards.json"
verified: "09dd8a3b95112d71770a0dac3f303c93259eb749707081b33a594fadc5148542"
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
| Fixture files | 573 |
| Cards | 5924 |
| Rulings | 17671 |
| Modules inspected | 918 |
| Static requirements | 2766 |
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
| compiler-cardprogram | 95 | closed |
| core-domain | 14 | closed |
| counter-continuous-effects | 64 | closed |
| deterministic-game-regressions | 6 | closed |
| events-replacement-zone | 60 | closed |
| functional-01 | 65 | closed |
| functional-02 | 66 | closed |
| functional-03 | 81 | closed |
| functional-04 | 71 | closed |
| functional-05 | 56 | closed |
| functional-06 | 61 | closed |
| functional-07 | 68 | closed |
| functional-08 | 120 | closed |
| functional-09 | 93 | closed |
| functional-10 | 63 | closed |
| functional-11 | 72 | closed |
| functional-12 | 68 | closed |
| generated-validation | 34 | closed |
| main-integration-smoke | 3 | closed |
| main-smoke | 6 | closed |
| merge-core | 9 | closed |
| multiplayer-commander | 17 | closed |
| nightly-property | 3 | closed |
| server-replay-privacy | 37 | closed |
| state-actions-damage | 22 | closed |
| targets-choices-continuations | 39 | closed |
| triggers-turns-exact-decks | 20 | closed |
| windows-compat | 18 | closed |

The JSON companion contains canonical identities, fixture owners, source
provenance, unresolved dynamics, and exact missing dependencies.
