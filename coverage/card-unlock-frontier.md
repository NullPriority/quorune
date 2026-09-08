---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "1fc997c261f6ed1fe2abbb36b1fe386abb9310061fc513ec41aa6a21fe100814"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":9544,"partial":11149,"unresolved":10930}`
- CardProgram states: `{"residual":22079,"trusted":9544}`
- Hard construction failures: 0
- Frontier fingerprint: `1fc997c261f6ed1fe2abbb36b1fe386abb9310061fc513ec41aa6a21fe100814`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 5,087 | 4,263 | 2,174 | 5,087 | missing_lowering | very_high |
| `keyword_dependency:banding` | 24 | 24 | 19 | 24 | missing_contract | medium |
| `effect_clause:typed-spell-additional-cost-clause` | 106 | 106 | 18 | 18 | missing_lowering | high |
| `effect_clause:life-change` | 477 | 475 | 16 | 42 | missing_lowering | high |
| `activated_effect:put-onto-battlefield` | 221 | 219 | 16 | 33 | missing_lowering | high |
| `effect_clause:exile` | 551 | 539 | 14 | 72 | missing_lowering | high |
| `keyword_dependency:start-your-engines` | 40 | 40 | 14 | 40 | missing_contract | medium |
| `effect_clause:create-token` | 543 | 528 | 13 | 73 | missing_lowering | high |
| `replacement:damage-prevention` | 140 | 138 | 13 | 28 | missing_lowering | very_high |
| `keyword_dependency:equip` | 25 | 25 | 13 | 25 | missing_contract | medium |
| `activated_effect:create-token` | 283 | 276 | 12 | 48 | missing_lowering | high |
| `effect_clause:unparsed-splice-onto-arcane` | 22 | 22 | 12 | 22 | missing_lowering | high |
| `keyword_dependency:living-weapon` | 19 | 19 | 12 | 19 | missing_contract | medium |
| `effect_clause:sacrifice` | 110 | 110 | 11 | 34 | missing_lowering | high |
| `keyword_dependency:rebound` | 34 | 34 | 11 | 34 | missing_contract | medium |
| `effect_clause:return` | 534 | 518 | 11 | 23 | missing_lowering | high |
| `keyword_dependency:myriad` | 23 | 23 | 11 | 23 | missing_contract | medium |
| `activated_effect:unparsed-this-creature-can` | 20 | 20 | 11 | 12 | missing_lowering | high |
| `keyword_dependency:umbra-armor` | 15 | 15 | 10 | 15 | missing_contract | medium |
| `keyword_dependency:extort` | 18 | 17 | 9 | 18 | missing_contract | medium |
| `activated_effect:life-change` | 182 | 172 | 9 | 17 | missing_lowering | high |
| `keyword_dependency:for-mirrodin` | 13 | 13 | 9 | 13 | missing_contract | medium |
| `keyword_dependency:split-second` | 21 | 21 | 8 | 21 | missing_contract | medium |
| `keyword_dependency:assist` | 16 | 16 | 8 | 16 | missing_contract | medium |
| `keyword_dependency:enlist` | 12 | 12 | 8 | 12 | missing_contract | medium |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:start-your-engines` | 2,216 | 5,151 | 5,151 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:create-token` | 2,214 | 5,175 | 5,184 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:put-onto-battlefield, keyword_dependency:start-your-engines` | 2,214 | 5,160 | 5,160 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:equip` | 2,214 | 5,152 | 5,152 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:start-your-engines` | 2,214 | 5,145 | 5,233 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:life-change, keyword_dependency:start-your-engines` | 2,212 | 5,169 | 5,169 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:create-token` | 2,212 | 5,159 | 5,168 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:myriad` | 2,212 | 5,150 | 5,150 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:put-onto-battlefield` | 2,212 | 5,144 | 5,144 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:equip` | 2,212 | 5,136 | 5,136 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:typed-spell-additional-cost-clause` | 2,212 | 5,129 | 5,217 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:exile, keyword_dependency:start-your-engines` | 2,210 | 5,199 | 5,199 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:put-onto-battlefield, activated_effect:create-token` | 2,210 | 5,168 | 5,177 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:equip, activated_effect:create-token` | 2,210 | 5,160 | 5,169 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:create-token` | 2,210 | 5,153 | 5,250 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:life-change` | 2,210 | 5,153 | 5,153 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:living-weapon` | 2,210 | 5,146 | 5,146 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:put-onto-battlefield, keyword_dependency:equip` | 2,210 | 5,145 | 5,145 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:put-onto-battlefield` | 2,210 | 5,138 | 5,226 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:myriad` | 2,210 | 5,134 | 5,134 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
