---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "56fb83476cef46a36740098723a277fd52da430372545bc7b017de8f76d1e0c7"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":7153,"partial":11810,"unresolved":12660}`
- CardProgram states: `{"residual":24470,"trusted":7153}`
- Hard construction failures: 0
- Frontier fingerprint: `56fb83476cef46a36740098723a277fd52da430372545bc7b017de8f76d1e0c7`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 8,108 | 6,565 | 3,780 | 8,108 | missing_lowering | very_high |
| `replacement:damage-prevention` | 167 | 164 | 23 | 43 | missing_lowering | very_high |
| `activated_effect:create-token` | 316 | 309 | 22 | 66 | missing_lowering | high |
| `keyword_dependency:banding` | 24 | 24 | 19 | 24 | missing_contract | medium |
| `effect_clause:create-token` | 564 | 549 | 17 | 84 | missing_lowering | high |
| `effect_clause:typed-spell-additional-cost-clause` | 106 | 106 | 17 | 17 | missing_lowering | high |
| `effect_clause:life-change` | 508 | 505 | 16 | 44 | missing_lowering | high |
| `activated_effect:put-onto-battlefield` | 221 | 219 | 14 | 32 | missing_lowering | high |
| `effect_clause:exile` | 572 | 559 | 12 | 74 | missing_lowering | high |
| `activated_effect:unparsed-investigate` | 13 | 13 | 12 | 13 | missing_lowering | high |
| `effect_clause:sacrifice` | 110 | 110 | 11 | 34 | missing_lowering | high |
| `keyword_dependency:rebound` | 34 | 34 | 11 | 34 | missing_contract | medium |
| `effect_clause:return` | 608 | 588 | 11 | 23 | missing_lowering | high |
| `effect_clause:unparsed-splice-onto-arcane` | 22 | 22 | 11 | 22 | missing_lowering | high |
| `effect_clause:unparsed-buyback-3` | 17 | 17 | 11 | 17 | missing_lowering | high |
| `activated_effect:unparsed-this-creature-can` | 20 | 20 | 11 | 12 | missing_lowering | high |
| `keyword_dependency:myriad` | 23 | 23 | 10 | 23 | missing_contract | medium |
| `keyword_dependency:retrace` | 17 | 17 | 10 | 17 | missing_contract | medium |
| `keyword_dependency:start-your-engines` | 40 | 40 | 9 | 40 | missing_contract | medium |
| `keyword_dependency:equip` | 25 | 25 | 9 | 25 | missing_contract | medium |
| `activated_effect:life-change` | 204 | 193 | 9 | 17 | missing_lowering | high |
| `keyword_dependency:living-weapon` | 19 | 19 | 8 | 19 | missing_contract | medium |
| `keyword_dependency:extort` | 18 | 17 | 8 | 18 | missing_contract | medium |
| `keyword_dependency:umbra-armor` | 15 | 15 | 8 | 15 | missing_contract | medium |
| `keyword_dependency:enlist` | 12 | 12 | 8 | 12 | missing_contract | medium |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:start-your-engines` | 3,834 | 8,214 | 8,228 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, activated_effect:create-token` | 3,833 | 8,217 | 8,233 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:banding` | 3,829 | 8,198 | 8,212 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, keyword_dependency:start-your-engines` | 3,829 | 8,191 | 8,193 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:create-token` | 3,827 | 8,258 | 8,272 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:equip` | 3,827 | 8,199 | 8,213 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:typed-spell-additional-cost-clause` | 3,826 | 8,191 | 8,294 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:life-change` | 3,825 | 8,218 | 8,232 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, activated_effect:put-onto-battlefield` | 3,825 | 8,206 | 8,221 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:myriad` | 3,825 | 8,197 | 8,211 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:start-your-engines` | 3,825 | 8,172 | 8,172 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, keyword_dependency:banding` | 3,824 | 8,175 | 8,177 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, keyword_dependency:start-your-engines` | 3,823 | 8,232 | 8,232 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:living-weapon` | 3,823 | 8,193 | 8,207 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:equip` | 3,823 | 8,173 | 8,173 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, effect_clause:create-token` | 3,822 | 8,235 | 8,237 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:umbra-armor` | 3,822 | 8,189 | 8,203 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, keyword_dependency:equip` | 3,822 | 8,176 | 8,178 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:start-your-engines` | 3,822 | 8,165 | 8,254 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:exile` | 3,821 | 8,248 | 8,262 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
