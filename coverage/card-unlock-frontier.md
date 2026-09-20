---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "dc38ee5368190ccac199a7cc3e5f8b8528492913cbee5aea410ac82517c204af"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":10577,"partial":10798,"unresolved":10248}`
- CardProgram states: `{"residual":21046,"trusted":10577}`
- Hard construction failures: 0
- Frontier fingerprint: `dc38ee5368190ccac199a7cc3e5f8b8528492913cbee5aea410ac82517c204af`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 4,542 | 3,826 | 2,024 | 4,542 | missing_lowering | very_high |
| `keyword_dependency:banding` | 24 | 24 | 19 | 24 | missing_contract | medium |
| `effect_clause:typed-spell-additional-cost-clause` | 106 | 106 | 18 | 18 | missing_lowering | high |
| `activated_effect:put-onto-battlefield` | 209 | 207 | 17 | 33 | missing_lowering | high |
| `effect_clause:life-change` | 468 | 466 | 16 | 41 | missing_lowering | high |
| `keyword_dependency:start-your-engines` | 40 | 40 | 15 | 40 | missing_contract | medium |
| `replacement:damage-prevention` | 140 | 138 | 15 | 30 | missing_lowering | very_high |
| `effect_clause:create-token` | 538 | 523 | 13 | 73 | missing_lowering | high |
| `activated_effect:create-token` | 283 | 276 | 13 | 50 | missing_lowering | high |
| `effect_clause:unparsed-splice-onto-arcane` | 22 | 22 | 13 | 22 | missing_lowering | high |
| `keyword_dependency:myriad` | 23 | 23 | 11 | 23 | missing_contract | medium |
| `mechanic_dependency:fading-remaining-lifecycle` | 17 | 17 | 11 | 17 | missing_contract | high |
| `activated_effect:unparsed-this-creature-can` | 20 | 20 | 11 | 12 | missing_lowering | high |
| `effect_clause:return` | 484 | 471 | 10 | 20 | missing_lowering | high |
| `keyword_dependency:umbra-armor` | 15 | 15 | 10 | 15 | missing_contract | medium |
| `keyword_dependency:extort` | 18 | 17 | 9 | 18 | missing_contract | medium |
| `activated_effect:life-change` | 180 | 170 | 9 | 17 | missing_lowering | high |
| `keyword_dependency:assist` | 16 | 16 | 9 | 16 | missing_contract | medium |
| `keyword_dependency:enlist` | 12 | 12 | 9 | 12 | missing_contract | medium |
| `keyword_dependency:split-second` | 21 | 21 | 8 | 21 | missing_contract | medium |
| `keyword_dependency:learn` | 13 | 13 | 8 | 13 | missing_contract | medium |
| `effect_clause:add-mana` | 57 | 57 | 8 | 11 | missing_lowering | high |
| `effect_clause:exile` | 525 | 514 | 7 | 52 | missing_lowering | high |
| `effect_clause:destroy-mass` | 141 | 132 | 7 | 18 | missing_lowering | high |
| `keyword_dependency:cipher` | 15 | 15 | 7 | 15 | missing_contract | medium |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:start-your-engines` | 2,066 | 4,606 | 4,606 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:create-token` | 2,065 | 4,632 | 4,639 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:put-onto-battlefield, keyword_dependency:start-your-engines` | 2,065 | 4,615 | 4,615 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:start-your-engines` | 2,064 | 4,600 | 4,688 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:create-token` | 2,063 | 4,616 | 4,623 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:put-onto-battlefield` | 2,063 | 4,599 | 4,599 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:put-onto-battlefield, activated_effect:create-token` | 2,062 | 4,625 | 4,632 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:life-change, keyword_dependency:start-your-engines` | 2,062 | 4,623 | 4,623 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:myriad` | 2,062 | 4,605 | 4,605 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:typed-spell-additional-cost-clause` | 2,062 | 4,584 | 4,672 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, replacement:damage-prevention` | 2,061 | 4,612 | 4,612 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:create-token` | 2,061 | 4,610 | 4,705 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:put-onto-battlefield` | 2,061 | 4,593 | 4,681 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:life-change` | 2,060 | 4,607 | 4,607 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:myriad` | 2,060 | 4,589 | 4,589 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, effect_clause:create-token` | 2,059 | 4,655 | 4,655 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:life-change, activated_effect:create-token` | 2,059 | 4,633 | 4,640 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:put-onto-battlefield, effect_clause:life-change` | 2,059 | 4,616 | 4,616 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:myriad` | 2,059 | 4,615 | 4,622 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, effect_clause:unparsed-splice-onto-arcane` | 2,059 | 4,604 | 4,604 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
