---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "8220f8980f7288285e565fa7b552fcdb0b255b4e1676d7231221da673ee8f916"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":10681,"partial":10771,"unresolved":10171}`
- CardProgram states: `{"residual":20942,"trusted":10681}`
- Hard construction failures: 0
- Frontier fingerprint: `8220f8980f7288285e565fa7b552fcdb0b255b4e1676d7231221da673ee8f916`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 4,599 | 3,853 | 2,037 | 4,599 | missing_lowering | very_high |
| `keyword_dependency:banding` | 24 | 24 | 19 | 24 | missing_contract | medium |
| `effect_clause:typed-spell-additional-cost-clause` | 106 | 106 | 18 | 18 | missing_lowering | high |
| `activated_effect:put-onto-battlefield` | 209 | 207 | 17 | 33 | missing_lowering | high |
| `effect_clause:life-change` | 468 | 466 | 16 | 41 | missing_lowering | high |
| `keyword_dependency:start-your-engines` | 40 | 40 | 16 | 40 | missing_contract | medium |
| `replacement:damage-prevention` | 140 | 138 | 15 | 30 | missing_lowering | very_high |
| `effect_clause:create-token` | 538 | 523 | 13 | 73 | missing_lowering | high |
| `activated_effect:create-token` | 283 | 276 | 13 | 50 | missing_lowering | high |
| `effect_clause:unparsed-splice-onto-arcane` | 22 | 22 | 12 | 22 | missing_lowering | high |
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
| `mechanic_dependency:vanishing-remaining-lifecycle` | 20 | 20 | 7 | 18 | missing_contract | high |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:start-your-engines` | 2,080 | 4,663 | 4,663 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:create-token` | 2,079 | 4,689 | 4,696 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:put-onto-battlefield, keyword_dependency:start-your-engines` | 2,079 | 4,672 | 4,672 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:start-your-engines` | 2,078 | 4,657 | 4,745 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:life-change, keyword_dependency:start-your-engines` | 2,076 | 4,680 | 4,680 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:create-token` | 2,076 | 4,673 | 4,680 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:myriad` | 2,076 | 4,662 | 4,662 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:put-onto-battlefield` | 2,076 | 4,656 | 4,656 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:put-onto-battlefield, activated_effect:create-token` | 2,075 | 4,682 | 4,689 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, replacement:damage-prevention` | 2,075 | 4,669 | 4,669 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:typed-spell-additional-cost-clause` | 2,075 | 4,641 | 4,729 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:create-token` | 2,074 | 4,667 | 4,762 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:put-onto-battlefield` | 2,074 | 4,650 | 4,738 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, effect_clause:create-token` | 2,073 | 4,712 | 4,712 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:life-change` | 2,073 | 4,664 | 4,664 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:umbra-armor` | 2,073 | 4,654 | 4,654 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:myriad` | 2,073 | 4,646 | 4,646 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:life-change, activated_effect:create-token` | 2,072 | 4,690 | 4,697 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:put-onto-battlefield, effect_clause:life-change` | 2,072 | 4,673 | 4,673 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:myriad` | 2,072 | 4,672 | 4,679 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
