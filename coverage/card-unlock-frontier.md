---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "30a4ec3cde6a6d16d8a293083b28913ac92403361e7a8a2b3fecbc1a986eae6d"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":10979,"partial":10717,"unresolved":9927}`
- CardProgram states: `{"residual":20644,"trusted":10979}`
- Hard construction failures: 0
- Frontier fingerprint: `30a4ec3cde6a6d16d8a293083b28913ac92403361e7a8a2b3fecbc1a986eae6d`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 4,081 | 3,456 | 1,821 | 4,081 | missing_lowering | very_high |
| `keyword_dependency:banding` | 24 | 24 | 19 | 24 | missing_contract | medium |
| `effect_clause:typed-spell-additional-cost-clause` | 106 | 106 | 18 | 18 | missing_lowering | high |
| `activated_effect:put-onto-battlefield` | 209 | 207 | 17 | 33 | missing_lowering | high |
| `effect_clause:life-change` | 468 | 466 | 16 | 41 | missing_lowering | high |
| `keyword_dependency:start-your-engines` | 40 | 40 | 16 | 40 | missing_contract | medium |
| `replacement:damage-prevention` | 140 | 138 | 15 | 30 | missing_lowering | very_high |
| `activated_effect:create-token` | 266 | 259 | 13 | 39 | missing_lowering | high |
| `effect_clause:unparsed-splice-onto-arcane` | 22 | 22 | 12 | 22 | missing_lowering | high |
| `mechanic_dependency:fading-remaining-lifecycle` | 17 | 17 | 11 | 17 | missing_contract | high |
| `activated_effect:unparsed-this-creature-can` | 20 | 20 | 11 | 12 | missing_lowering | high |
| `effect_clause:return` | 484 | 471 | 10 | 20 | missing_lowering | high |
| `keyword_dependency:umbra-armor` | 15 | 15 | 10 | 15 | missing_contract | medium |
| `keyword_dependency:extort` | 18 | 17 | 9 | 18 | missing_contract | medium |
| `activated_effect:life-change` | 180 | 170 | 9 | 17 | missing_lowering | high |
| `keyword_dependency:assist` | 16 | 16 | 9 | 16 | missing_contract | medium |
| `keyword_dependency:learn` | 13 | 13 | 9 | 13 | missing_contract | medium |
| `keyword_dependency:enlist` | 12 | 12 | 9 | 12 | missing_contract | medium |
| `effect_clause:create-token` | 525 | 510 | 8 | 64 | missing_lowering | high |
| `keyword_dependency:split-second` | 21 | 21 | 8 | 21 | missing_contract | medium |
| `activated_effect:exile` | 291 | 274 | 8 | 20 | missing_lowering | high |
| `effect_clause:add-mana` | 57 | 57 | 8 | 11 | missing_lowering | high |
| `effect_clause:exile` | 525 | 514 | 7 | 52 | missing_lowering | high |
| `effect_clause:destroy-mass` | 141 | 132 | 7 | 18 | missing_lowering | high |
| `mechanic_dependency:vanishing-remaining-lifecycle` | 20 | 20 | 7 | 18 | missing_contract | high |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:start-your-engines` | 1,864 | 4,145 | 4,145 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:put-onto-battlefield, keyword_dependency:start-your-engines` | 1,863 | 4,154 | 4,154 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:start-your-engines` | 1,862 | 4,139 | 4,227 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:life-change, keyword_dependency:start-your-engines` | 1,860 | 4,162 | 4,162 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:put-onto-battlefield` | 1,860 | 4,138 | 4,138 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, replacement:damage-prevention` | 1,859 | 4,151 | 4,151 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:typed-spell-additional-cost-clause` | 1,859 | 4,123 | 4,211 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:create-token` | 1,858 | 4,160 | 4,167 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:put-onto-battlefield` | 1,858 | 4,132 | 4,220 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:life-change` | 1,857 | 4,146 | 4,146 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:umbra-armor` | 1,857 | 4,136 | 4,136 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:put-onto-battlefield, effect_clause:life-change` | 1,856 | 4,155 | 4,155 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, effect_clause:unparsed-splice-onto-arcane` | 1,856 | 4,143 | 4,143 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, replacement:damage-prevention` | 1,856 | 4,135 | 4,135 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:put-onto-battlefield, replacement:damage-prevention` | 1,855 | 4,144 | 4,144 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:create-token` | 1,855 | 4,144 | 4,151 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, effect_clause:life-change` | 1,855 | 4,140 | 4,228 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:extort` | 1,855 | 4,139 | 4,139 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, mechanic_dependency:fading-remaining-lifecycle` | 1,855 | 4,138 | 4,138 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:unparsed-this-creature-can` | 1,855 | 4,133 | 4,134 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
