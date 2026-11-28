---
title: "Compact CI card dependencies"
status: "generated"
authoritative_source: "tests/fixtures/compact-ci-fixtures.json and platform/test-shards.json"
verified: "c612fe3886182161bed3f96662b49cc0112ab987086b52a4bcde8e377a096f29"
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
| Fixture files | 1648 |
| Cards | 9607 |
| Rulings | 34032 |
| Modules inspected | 2003 |
| Static requirements | 150914 |
| Declared dynamic requirements | 13817 |
| Unresolved dynamic sites | 0 |
| Missing cards | 0 |
| Missing deck dependencies | 0 |
| Fixture identity conflicts | 0 |

## Shard closure

| Shard | Modules | Status |
| --- | ---: | --- |
| casting-costs-mana | 95 | closed |
| combat-declarations | 29 | closed |
| compiler-cardprogram | 155 | closed |
| core-domain | 14 | closed |
| counter-continuous-effects | 67 | closed |
| deterministic-game-regressions | 6 | closed |
| events-replacement-zone | 89 | closed |
| functional-01 | 131 | closed |
| functional-02 | 166 | closed |
| functional-03 | 261 | closed |
| functional-04 | 143 | closed |
| functional-05 | 137 | closed |
| functional-06 | 167 | closed |
| functional-07 | 174 | closed |
| functional-08 | 226 | closed |
| functional-09 | 141 | closed |
| functional-10 | 167 | closed |
| functional-11 | 111 | closed |
| functional-12 | 145 | closed |
| generated-validation | 34 | closed |
| main-integration-smoke | 3 | closed |
| main-smoke | 6 | closed |
| merge-core | 9 | closed |
| multiplayer-commander | 19 | closed |
| nightly-property | 3 | closed |
| server-replay-privacy | 70 | closed |
| state-actions-damage | 23 | closed |
| targets-choices-continuations | 89 | closed |
| triggers-turns-exact-decks | 22 | closed |
| windows-compat | 18 | closed |

The JSON companion contains canonical identities, fixture owners, source
provenance, unresolved dynamics, and exact missing dependencies.
