---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "5c4fe5a2f95be2f8cf05fc07cabe0ea4ec81ecb05fc2cb0aec79cb0399e97a04"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":8913,"partial":11322,"unresolved":11388}`
- CardProgram states: `{"residual":22710,"trusted":8913}`
- Hard construction failures: 0
- Frontier fingerprint: `5c4fe5a2f95be2f8cf05fc07cabe0ea4ec81ecb05fc2cb0aec79cb0399e97a04`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 5,415 | 4,554 | 2,322 | 5,415 | missing_lowering | very_high |
| `keyword_dependency:banding` | 24 | 24 | 19 | 24 | missing_contract | medium |
| `effect_clause:typed-spell-additional-cost-clause` | 106 | 106 | 18 | 18 | missing_lowering | high |
| `effect_clause:life-change` | 496 | 494 | 16 | 43 | missing_lowering | high |
| `effect_clause:exile` | 552 | 540 | 14 | 73 | missing_lowering | high |
| `keyword_dependency:start-your-engines` | 40 | 40 | 14 | 40 | missing_contract | medium |
| `activated_effect:put-onto-battlefield` | 221 | 219 | 14 | 32 | missing_lowering | high |
| `effect_clause:create-token` | 546 | 531 | 13 | 73 | missing_lowering | high |
| `replacement:damage-prevention` | 142 | 140 | 13 | 29 | missing_lowering | very_high |
| `keyword_dependency:equip` | 25 | 25 | 12 | 25 | missing_contract | medium |
| `keyword_dependency:living-weapon` | 19 | 19 | 12 | 19 | missing_contract | medium |
| `activated_effect:create-token` | 290 | 283 | 11 | 47 | missing_lowering | high |
| `effect_clause:sacrifice` | 110 | 110 | 11 | 34 | missing_lowering | high |
| `keyword_dependency:rebound` | 34 | 34 | 11 | 34 | missing_contract | medium |
| `effect_clause:return` | 568 | 548 | 11 | 23 | missing_lowering | high |
| `keyword_dependency:myriad` | 23 | 23 | 11 | 23 | missing_contract | medium |
| `effect_clause:unparsed-splice-onto-arcane` | 22 | 22 | 11 | 22 | missing_lowering | high |
| `activated_effect:unparsed-this-creature-can` | 20 | 20 | 11 | 12 | missing_lowering | high |
| `keyword_dependency:umbra-armor` | 15 | 15 | 10 | 15 | missing_contract | medium |
| `activated_effect:life-change` | 202 | 191 | 9 | 17 | missing_lowering | high |
| `keyword_dependency:split-second` | 21 | 21 | 8 | 21 | missing_contract | medium |
| `keyword_dependency:extort` | 18 | 17 | 8 | 18 | missing_contract | medium |
| `keyword_dependency:for-mirrodin` | 13 | 13 | 8 | 13 | missing_contract | medium |
| `keyword_dependency:enlist` | 12 | 12 | 8 | 12 | missing_contract | medium |
| `effect_clause:add-mana` | 57 | 57 | 8 | 11 | missing_lowering | high |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:start-your-engines` | 2,364 | 5,479 | 5,479 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:equip` | 2,362 | 5,480 | 5,480 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:start-your-engines` | 2,362 | 5,473 | 5,561 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:create-token` | 2,360 | 5,502 | 5,512 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:life-change, keyword_dependency:start-your-engines` | 2,360 | 5,498 | 5,498 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:put-onto-battlefield` | 2,360 | 5,487 | 5,488 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:myriad` | 2,360 | 5,478 | 5,478 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:equip` | 2,360 | 5,464 | 5,464 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:typed-spell-additional-cost-clause` | 2,360 | 5,457 | 5,545 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:exile, keyword_dependency:start-your-engines` | 2,358 | 5,528 | 5,528 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:create-token` | 2,358 | 5,486 | 5,496 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:life-change` | 2,358 | 5,482 | 5,482 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:living-weapon` | 2,358 | 5,474 | 5,474 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:put-onto-battlefield` | 2,358 | 5,471 | 5,472 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:myriad` | 2,358 | 5,462 | 5,462 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:equip` | 2,358 | 5,458 | 5,546 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, effect_clause:create-token` | 2,357 | 5,528 | 5,528 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, replacement:damage-prevention` | 2,357 | 5,484 | 5,486 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:umbra-armor` | 2,357 | 5,470 | 5,470 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:exile` | 2,356 | 5,512 | 5,512 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
