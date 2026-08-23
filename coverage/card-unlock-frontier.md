---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "db91b5ef17a92d6178d8d016ecce6bf614d46144a32d00ae2604b29792de4f30"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":6088,"partial":11988,"unresolved":13547}`
- CardProgram states: `{"residual":25535,"trusted":6088}`
- Hard construction failures: 0
- Frontier fingerprint: `db91b5ef17a92d6178d8d016ecce6bf614d46144a32d00ae2604b29792de4f30`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 8,440 | 6,791 | 3,791 | 8,440 | missing_lowering | very_high |
| `activated_effect:create-token` | 327 | 320 | 22 | 66 | missing_lowering | high |
| `replacement:damage-prevention` | 168 | 165 | 21 | 40 | missing_lowering | very_high |
| `effect_clause:create-token` | 577 | 561 | 17 | 87 | missing_lowering | high |
| `effect_clause:typed-spell-additional-cost-clause` | 106 | 106 | 17 | 17 | missing_lowering | high |
| `effect_clause:life-change` | 553 | 550 | 16 | 44 | missing_lowering | high |
| `activated_effect:unparsed-this-creature-can` | 39 | 39 | 16 | 23 | missing_lowering | high |
| `activated_effect:put-onto-battlefield` | 221 | 219 | 14 | 32 | missing_lowering | high |
| `activated_effect:unparsed-investigate` | 13 | 13 | 12 | 13 | missing_lowering | high |
| `effect_clause:exile` | 604 | 584 | 11 | 84 | missing_lowering | high |
| `effect_clause:sacrifice` | 110 | 110 | 11 | 34 | missing_lowering | high |
| `effect_clause:return` | 626 | 601 | 11 | 23 | missing_lowering | high |
| `effect_clause:unparsed-splice-onto-arcane` | 22 | 22 | 11 | 22 | missing_lowering | high |
| `keyword_dependency:rebound` | 34 | 34 | 10 | 34 | missing_contract | medium |
| `effect_clause:unparsed-buyback-3` | 17 | 17 | 10 | 17 | missing_lowering | high |
| `effect_clause:unparsed-target-creature-can` | 17 | 17 | 10 | 13 | missing_lowering | high |
| `keyword_dependency:banding` | 13 | 13 | 10 | 13 | missing_contract | medium |
| `activated_effect:unparsed-regenerate-enchanted-creature` | 15 | 15 | 9 | 11 | missing_lowering | high |
| `keyword_dependency:start-your-engines` | 40 | 40 | 8 | 40 | missing_contract | medium |
| `keyword_dependency:living-weapon` | 19 | 19 | 8 | 19 | missing_contract | medium |
| `activated_effect:life-change` | 221 | 209 | 8 | 18 | missing_lowering | high |
| `keyword_dependency:retrace` | 17 | 17 | 8 | 17 | missing_contract | medium |
| `keyword_dependency:umbra-armor` | 15 | 15 | 8 | 15 | missing_contract | medium |
| `keyword_dependency:enlist` | 12 | 12 | 8 | 12 | missing_contract | medium |
| `effect_clause:add-mana` | 57 | 57 | 8 | 11 | missing_lowering | high |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:start-your-engines` | 3,843 | 8,546 | 8,560 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, replacement:damage-prevention` | 3,842 | 8,546 | 8,566 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:create-token` | 3,838 | 8,593 | 8,607 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:equip` | 3,838 | 8,531 | 8,545 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, activated_effect:unparsed-this-creature-can` | 3,838 | 8,529 | 8,552 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:typed-spell-additional-cost-clause` | 3,837 | 8,523 | 8,626 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:life-change` | 3,836 | 8,550 | 8,564 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, activated_effect:put-onto-battlefield` | 3,836 | 8,538 | 8,553 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, keyword_dependency:start-your-engines` | 3,836 | 8,520 | 8,526 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:living-weapon` | 3,834 | 8,525 | 8,539 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:myriad` | 3,833 | 8,529 | 8,543 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:umbra-armor` | 3,833 | 8,521 | 8,535 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, keyword_dependency:start-your-engines` | 3,832 | 8,567 | 8,567 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, activated_effect:unparsed-investigate` | 3,832 | 8,519 | 8,533 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:equip` | 3,832 | 8,505 | 8,505 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:unparsed-this-creature-can, keyword_dependency:start-your-engines` | 3,832 | 8,503 | 8,512 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:exile` | 3,831 | 8,590 | 8,604 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, effect_clause:create-token` | 3,831 | 8,567 | 8,573 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:sacrifice` | 3,831 | 8,540 | 8,554 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:return` | 3,831 | 8,529 | 8,543 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
