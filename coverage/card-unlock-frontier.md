---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "6f41bb0003dafdaf556decbaa0a940d0bf2296434c7273187f1b6f5542c9bb85"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":6727,"partial":11908,"unresolved":12988}`
- CardProgram states: `{"residual":24896,"trusted":6727}`
- Hard construction failures: 0
- Frontier fingerprint: `6f41bb0003dafdaf556decbaa0a940d0bf2296434c7273187f1b6f5542c9bb85`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 8,121 | 6,573 | 3,740 | 8,121 | missing_lowering | very_high |
| `replacement:damage-prevention` | 167 | 164 | 23 | 43 | missing_lowering | very_high |
| `activated_effect:create-token` | 316 | 309 | 22 | 66 | missing_lowering | high |
| `keyword_dependency:banding` | 24 | 24 | 19 | 24 | missing_contract | medium |
| `effect_clause:create-token` | 564 | 549 | 17 | 84 | missing_lowering | high |
| `activated_effect:unparsed-this-creature-can` | 39 | 39 | 17 | 25 | missing_lowering | high |
| `effect_clause:typed-spell-additional-cost-clause` | 106 | 106 | 17 | 17 | missing_lowering | high |
| `effect_clause:life-change` | 511 | 508 | 16 | 44 | missing_lowering | high |
| `activated_effect:put-onto-battlefield` | 221 | 219 | 14 | 32 | missing_lowering | high |
| `effect_clause:exile` | 588 | 575 | 12 | 74 | missing_lowering | high |
| `activated_effect:unparsed-regenerate-enchanted-creature` | 15 | 15 | 12 | 14 | missing_lowering | high |
| `activated_effect:unparsed-investigate` | 13 | 13 | 12 | 13 | missing_lowering | high |
| `effect_clause:sacrifice` | 110 | 110 | 11 | 34 | missing_lowering | high |
| `keyword_dependency:rebound` | 34 | 34 | 11 | 34 | missing_contract | medium |
| `effect_clause:return` | 611 | 591 | 11 | 23 | missing_lowering | high |
| `effect_clause:unparsed-splice-onto-arcane` | 22 | 22 | 11 | 22 | missing_lowering | high |
| `effect_clause:unparsed-buyback-3` | 17 | 17 | 10 | 17 | missing_lowering | high |
| `effect_clause:unparsed-target-creature-can` | 17 | 17 | 10 | 13 | missing_lowering | high |
| `keyword_dependency:start-your-engines` | 40 | 40 | 9 | 40 | missing_contract | medium |
| `keyword_dependency:equip` | 25 | 25 | 9 | 25 | missing_contract | medium |
| `keyword_dependency:retrace` | 17 | 17 | 9 | 17 | missing_contract | medium |
| `activated_effect:unparsed-regenerate-target-creature` | 19 | 19 | 9 | 10 | missing_lowering | high |
| `keyword_dependency:living-weapon` | 19 | 19 | 8 | 19 | missing_contract | medium |
| `keyword_dependency:extort` | 18 | 17 | 8 | 18 | missing_contract | medium |
| `activated_effect:life-change` | 204 | 193 | 8 | 17 | missing_lowering | high |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:start-your-engines` | 3,794 | 8,227 | 8,241 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, activated_effect:create-token` | 3,793 | 8,230 | 8,246 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:banding` | 3,789 | 8,211 | 8,225 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, keyword_dependency:start-your-engines` | 3,789 | 8,204 | 8,206 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, activated_effect:unparsed-this-creature-can` | 3,788 | 8,212 | 8,233 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:create-token` | 3,787 | 8,271 | 8,285 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:equip` | 3,787 | 8,212 | 8,226 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:typed-spell-additional-cost-clause` | 3,786 | 8,204 | 8,307 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:life-change` | 3,785 | 8,231 | 8,245 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, activated_effect:put-onto-battlefield` | 3,785 | 8,219 | 8,234 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:start-your-engines` | 3,785 | 8,185 | 8,185 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, keyword_dependency:banding` | 3,784 | 8,188 | 8,190 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:unparsed-this-creature-can, keyword_dependency:start-your-engines` | 3,784 | 8,186 | 8,193 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, keyword_dependency:start-your-engines` | 3,783 | 8,245 | 8,245 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:living-weapon` | 3,783 | 8,206 | 8,220 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, activated_effect:unparsed-this-creature-can` | 3,783 | 8,189 | 8,198 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:equip` | 3,783 | 8,186 | 8,186 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, effect_clause:create-token` | 3,782 | 8,248 | 8,250 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:myriad` | 3,782 | 8,210 | 8,224 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:umbra-armor` | 3,782 | 8,202 | 8,216 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
