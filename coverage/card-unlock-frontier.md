---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "c7bbb96d4ae6708897e9dd3e208586f7c960e7089d1e60e3611e43113d868a12"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":6893,"partial":11860,"unresolved":12870}`
- CardProgram states: `{"residual":24730,"trusted":6893}`
- Hard construction failures: 0
- Frontier fingerprint: `c7bbb96d4ae6708897e9dd3e208586f7c960e7089d1e60e3611e43113d868a12`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 8,117 | 6,571 | 3,757 | 8,117 | missing_lowering | very_high |
| `replacement:damage-prevention` | 167 | 164 | 23 | 43 | missing_lowering | very_high |
| `activated_effect:create-token` | 316 | 309 | 22 | 66 | missing_lowering | high |
| `keyword_dependency:banding` | 24 | 24 | 19 | 24 | missing_contract | medium |
| `activated_effect:unparsed-this-creature-can` | 39 | 39 | 18 | 25 | missing_lowering | high |
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
| `effect_clause:unparsed-buyback-3` | 17 | 17 | 10 | 17 | missing_lowering | high |
| `keyword_dependency:retrace` | 17 | 17 | 10 | 17 | missing_contract | medium |
| `effect_clause:unparsed-target-creature-can` | 17 | 17 | 10 | 13 | missing_lowering | high |
| `keyword_dependency:start-your-engines` | 40 | 40 | 9 | 40 | missing_contract | medium |
| `keyword_dependency:equip` | 25 | 25 | 9 | 25 | missing_contract | medium |
| `keyword_dependency:myriad` | 23 | 23 | 9 | 23 | missing_contract | medium |
| `activated_effect:life-change` | 204 | 193 | 9 | 17 | missing_lowering | high |
| `activated_effect:unparsed-regenerate-target-creature` | 19 | 19 | 9 | 10 | missing_lowering | high |
| `keyword_dependency:living-weapon` | 19 | 19 | 8 | 19 | missing_contract | medium |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:start-your-engines` | 3,811 | 8,223 | 8,237 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, activated_effect:create-token` | 3,810 | 8,226 | 8,242 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, activated_effect:unparsed-this-creature-can` | 3,806 | 8,208 | 8,229 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:banding` | 3,806 | 8,207 | 8,221 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, keyword_dependency:start-your-engines` | 3,806 | 8,200 | 8,202 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:create-token` | 3,804 | 8,267 | 8,281 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:equip` | 3,804 | 8,208 | 8,222 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:typed-spell-additional-cost-clause` | 3,803 | 8,200 | 8,303 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:life-change` | 3,802 | 8,227 | 8,241 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, activated_effect:put-onto-battlefield` | 3,802 | 8,215 | 8,230 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:unparsed-this-creature-can, keyword_dependency:start-your-engines` | 3,802 | 8,182 | 8,189 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:start-your-engines` | 3,802 | 8,181 | 8,181 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:myriad` | 3,801 | 8,206 | 8,220 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, activated_effect:unparsed-this-creature-can` | 3,801 | 8,185 | 8,194 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, keyword_dependency:banding` | 3,801 | 8,184 | 8,186 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, keyword_dependency:start-your-engines` | 3,800 | 8,241 | 8,241 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:living-weapon` | 3,800 | 8,202 | 8,216 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:equip` | 3,800 | 8,182 | 8,182 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, effect_clause:create-token` | 3,799 | 8,244 | 8,246 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:umbra-armor` | 3,799 | 8,198 | 8,212 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
