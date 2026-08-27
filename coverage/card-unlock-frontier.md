---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "47ad6cd7e657811d41121824237e9cf8eab7dc7f8fda786a7574b0b7607d4001"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":6928,"partial":11847,"unresolved":12848}`
- CardProgram states: `{"residual":24695,"trusted":6928}`
- Hard construction failures: 0
- Frontier fingerprint: `47ad6cd7e657811d41121824237e9cf8eab7dc7f8fda786a7574b0b7607d4001`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 8,114 | 6,570 | 3,760 | 8,114 | missing_lowering | very_high |
| `replacement:damage-prevention` | 167 | 164 | 23 | 43 | missing_lowering | very_high |
| `activated_effect:create-token` | 316 | 309 | 22 | 66 | missing_lowering | high |
| `keyword_dependency:banding` | 24 | 24 | 19 | 24 | missing_contract | medium |
| `effect_clause:create-token` | 564 | 549 | 17 | 84 | missing_lowering | high |
| `effect_clause:typed-spell-additional-cost-clause` | 106 | 106 | 17 | 17 | missing_lowering | high |
| `effect_clause:life-change` | 508 | 505 | 16 | 44 | missing_lowering | high |
| `activated_effect:put-onto-battlefield` | 221 | 219 | 14 | 32 | missing_lowering | high |
| `effect_clause:exile` | 572 | 559 | 12 | 74 | missing_lowering | high |
| `activated_effect:unparsed-regenerate-enchanted-creature` | 15 | 15 | 12 | 14 | missing_lowering | high |
| `activated_effect:unparsed-investigate` | 13 | 13 | 12 | 13 | missing_lowering | high |
| `effect_clause:sacrifice` | 110 | 110 | 11 | 34 | missing_lowering | high |
| `keyword_dependency:rebound` | 34 | 34 | 11 | 34 | missing_contract | medium |
| `effect_clause:return` | 609 | 589 | 11 | 23 | missing_lowering | high |
| `effect_clause:unparsed-splice-onto-arcane` | 22 | 22 | 11 | 22 | missing_lowering | high |
| `effect_clause:unparsed-buyback-3` | 17 | 17 | 11 | 17 | missing_lowering | high |
| `activated_effect:unparsed-this-creature-can` | 20 | 20 | 11 | 12 | missing_lowering | high |
| `keyword_dependency:retrace` | 17 | 17 | 10 | 17 | missing_contract | medium |
| `keyword_dependency:start-your-engines` | 40 | 40 | 9 | 40 | missing_contract | medium |
| `keyword_dependency:equip` | 25 | 25 | 9 | 25 | missing_contract | medium |
| `keyword_dependency:myriad` | 23 | 23 | 9 | 23 | missing_contract | medium |
| `activated_effect:life-change` | 204 | 193 | 9 | 17 | missing_lowering | high |
| `activated_effect:unparsed-regenerate-target-creature` | 19 | 19 | 9 | 10 | missing_lowering | high |
| `keyword_dependency:living-weapon` | 19 | 19 | 8 | 19 | missing_contract | medium |
| `keyword_dependency:extort` | 18 | 17 | 8 | 18 | missing_contract | medium |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:start-your-engines` | 3,814 | 8,220 | 8,234 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, activated_effect:create-token` | 3,813 | 8,223 | 8,239 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:banding` | 3,809 | 8,204 | 8,218 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, keyword_dependency:start-your-engines` | 3,809 | 8,197 | 8,199 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:create-token` | 3,807 | 8,264 | 8,278 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:equip` | 3,807 | 8,205 | 8,219 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:typed-spell-additional-cost-clause` | 3,806 | 8,197 | 8,300 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:life-change` | 3,805 | 8,224 | 8,238 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, activated_effect:put-onto-battlefield` | 3,805 | 8,212 | 8,227 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:start-your-engines` | 3,805 | 8,178 | 8,178 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:myriad` | 3,804 | 8,203 | 8,217 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, keyword_dependency:banding` | 3,804 | 8,181 | 8,183 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, keyword_dependency:start-your-engines` | 3,803 | 8,238 | 8,238 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:living-weapon` | 3,803 | 8,199 | 8,213 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:equip` | 3,803 | 8,179 | 8,179 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, effect_clause:create-token` | 3,802 | 8,241 | 8,243 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:umbra-armor` | 3,802 | 8,195 | 8,209 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, keyword_dependency:equip` | 3,802 | 8,182 | 8,184 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:start-your-engines` | 3,802 | 8,171 | 8,260 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:exile` | 3,801 | 8,254 | 8,268 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
