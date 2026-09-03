---
title: "Compact CI card dependencies"
status: "generated"
authoritative_source: "tests/fixtures/compact-ci-fixtures.json and platform/test-shards.json"
verified: "9d74104694e1c834ceb619ddcee42f3361ef3af2f97350feb31d8f78b3e58331"
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
| Fixture files | 41 |
| Cards | 577 |
| Rulings | 1097 |
| Modules inspected | 321 |
| Static requirements | 866 |
| Declared dynamic requirements | 12 |
| Unresolved dynamic sites | 0 |
| Missing cards | 0 |
| Missing deck dependencies | 0 |
| Fixture identity conflicts | 0 |

## Shard closure

| Shard | Modules | Status |
| --- | ---: | --- |
| casting-costs-mana | 47 | closed |
| combat-declarations | 21 | closed |
| compiler-cardprogram | 53 | closed |
| core-domain | 14 | closed |
| counter-continuous-effects | 29 | closed |
| deterministic-game-regressions | 5 | closed |
| events-replacement-zone | 36 | closed |
| functional-01 | 13 | closed |
| functional-02 | 52 | closed |
| functional-03 | 22 | closed |
| functional-04 | 19 | closed |
| functional-05 | 19 | closed |
| functional-06 | 27 | closed |
| functional-07 | 16 | closed |
| functional-08 | 17 | closed |
| functional-09 | 19 | closed |
| functional-10 | 20 | closed |
| functional-11 | 39 | closed |
| functional-12 | 26 | closed |
| generated-validation | 32 | closed |
| main-integration-smoke | 3 | closed |
| main-smoke | 6 | closed |
| merge-core | 9 | closed |
| multiplayer-commander | 9 | closed |
| nightly-property | 3 | closed |
| server-replay-privacy | 14 | closed |
| state-actions-damage | 17 | closed |
| targets-choices-continuations | 27 | closed |
| triggers-turns-exact-decks | 19 | closed |
| windows-compat | 10 | closed |

The JSON companion contains canonical identities, fixture owners, source
provenance, unresolved dynamics, and exact missing dependencies.
