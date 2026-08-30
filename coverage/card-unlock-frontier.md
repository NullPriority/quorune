---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "096be395dc5d6a1df9a40358cda559c67a8f45e12e55c840ea747aa123642ed3"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":8223,"partial":11538,"unresolved":11862}`
- CardProgram states: `{"residual":23400,"trusted":8223}`
- Hard construction failures: 0
- Frontier fingerprint: `096be395dc5d6a1df9a40358cda559c67a8f45e12e55c840ea747aa123642ed3`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 6,452 | 5,135 | 2,685 | 6,452 | missing_lowering | very_high |
| `activated_effect:create-token` | 314 | 307 | 24 | 66 | missing_lowering | high |
| `replacement:damage-prevention` | 167 | 164 | 24 | 43 | missing_lowering | very_high |
| `keyword_dependency:banding` | 24 | 24 | 19 | 24 | missing_contract | medium |
| `effect_clause:create-token` | 560 | 545 | 18 | 83 | missing_lowering | high |
| `effect_clause:typed-spell-additional-cost-clause` | 106 | 106 | 18 | 18 | missing_lowering | high |
| `effect_clause:life-change` | 500 | 498 | 16 | 44 | missing_lowering | high |
| `keyword_dependency:start-your-engines` | 40 | 40 | 14 | 40 | missing_contract | medium |
| `activated_effect:put-onto-battlefield` | 221 | 219 | 14 | 32 | missing_lowering | high |
| `effect_clause:exile` | 563 | 550 | 13 | 74 | missing_lowering | high |
| `activated_effect:unparsed-investigate` | 13 | 13 | 12 | 13 | missing_lowering | high |
| `effect_clause:sacrifice` | 110 | 110 | 11 | 34 | missing_lowering | high |
| `keyword_dependency:rebound` | 34 | 34 | 11 | 34 | missing_contract | medium |
| `keyword_dependency:equip` | 25 | 25 | 11 | 25 | missing_contract | medium |
| `effect_clause:return` | 568 | 548 | 11 | 23 | missing_lowering | high |
| `keyword_dependency:myriad` | 23 | 23 | 11 | 23 | missing_contract | medium |
| `effect_clause:unparsed-splice-onto-arcane` | 22 | 22 | 11 | 22 | missing_lowering | high |
| `keyword_dependency:living-weapon` | 19 | 19 | 11 | 19 | missing_contract | medium |
| `effect_clause:unparsed-buyback-3` | 17 | 17 | 11 | 17 | missing_lowering | high |
| `activated_effect:unparsed-this-creature-can` | 20 | 20 | 11 | 12 | missing_lowering | high |
| `keyword_dependency:retrace` | 17 | 17 | 10 | 17 | missing_contract | medium |
| `activated_effect:life-change` | 203 | 192 | 9 | 17 | missing_lowering | high |
| `keyword_dependency:split-second` | 21 | 21 | 8 | 21 | missing_contract | medium |
| `keyword_dependency:extort` | 18 | 17 | 8 | 18 | missing_contract | medium |
| `keyword_dependency:umbra-armor` | 15 | 15 | 8 | 15 | missing_contract | medium |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, replacement:damage-prevention` | 2,739 | 6,561 | 6,577 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:start-your-engines` | 2,737 | 6,558 | 6,572 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:banding` | 2,735 | 6,542 | 6,556 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:create-token` | 2,733 | 6,601 | 6,615 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:equip` | 2,733 | 6,543 | 6,557 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:typed-spell-additional-cost-clause` | 2,733 | 6,536 | 6,638 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:life-change` | 2,731 | 6,562 | 6,576 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, activated_effect:put-onto-battlefield` | 2,731 | 6,550 | 6,565 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:myriad` | 2,731 | 6,541 | 6,555 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, keyword_dependency:start-your-engines` | 2,731 | 6,535 | 6,537 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:living-weapon` | 2,729 | 6,537 | 6,551 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, keyword_dependency:banding` | 2,729 | 6,519 | 6,521 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:exile` | 2,728 | 6,592 | 6,606 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:umbra-armor` | 2,728 | 6,533 | 6,547 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, effect_clause:create-token` | 2,727 | 6,578 | 6,580 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, activated_effect:unparsed-investigate` | 2,727 | 6,531 | 6,545 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, keyword_dependency:equip` | 2,727 | 6,520 | 6,522 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:start-your-engines` | 2,727 | 6,516 | 6,516 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, effect_clause:typed-spell-additional-cost-clause` | 2,727 | 6,513 | 6,603 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:sacrifice` | 2,726 | 6,552 | 6,566 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
