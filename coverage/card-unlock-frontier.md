---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "42b26b4ae3f33fac2427e3f6016e2123c654d9c2540c1eaa4af359d8269c027e"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":7935,"partial":11642,"unresolved":12046}`
- CardProgram states: `{"residual":23688,"trusted":7935}`
- Hard construction failures: 0
- Frontier fingerprint: `42b26b4ae3f33fac2427e3f6016e2123c654d9c2540c1eaa4af359d8269c027e`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 7,709 | 6,222 | 3,580 | 7,709 | missing_lowering | very_high |
| `replacement:damage-prevention` | 167 | 164 | 23 | 43 | missing_lowering | very_high |
| `activated_effect:create-token` | 314 | 307 | 22 | 66 | missing_lowering | high |
| `keyword_dependency:banding` | 24 | 24 | 19 | 24 | missing_contract | medium |
| `effect_clause:create-token` | 560 | 545 | 18 | 83 | missing_lowering | high |
| `effect_clause:typed-spell-additional-cost-clause` | 106 | 106 | 18 | 18 | missing_lowering | high |
| `effect_clause:life-change` | 500 | 498 | 16 | 44 | missing_lowering | high |
| `activated_effect:put-onto-battlefield` | 221 | 219 | 14 | 32 | missing_lowering | high |
| `effect_clause:exile` | 563 | 550 | 13 | 74 | missing_lowering | high |
| `activated_effect:unparsed-investigate` | 13 | 13 | 12 | 13 | missing_lowering | high |
| `effect_clause:sacrifice` | 110 | 110 | 11 | 34 | missing_lowering | high |
| `keyword_dependency:rebound` | 34 | 34 | 11 | 34 | missing_contract | medium |
| `effect_clause:return` | 568 | 548 | 11 | 23 | missing_lowering | high |
| `effect_clause:unparsed-splice-onto-arcane` | 22 | 22 | 11 | 22 | missing_lowering | high |
| `effect_clause:unparsed-buyback-3` | 17 | 17 | 11 | 17 | missing_lowering | high |
| `activated_effect:unparsed-this-creature-can` | 20 | 20 | 11 | 12 | missing_lowering | high |
| `keyword_dependency:start-your-engines` | 40 | 40 | 10 | 40 | missing_contract | medium |
| `keyword_dependency:myriad` | 23 | 23 | 10 | 23 | missing_contract | medium |
| `keyword_dependency:retrace` | 17 | 17 | 10 | 17 | missing_contract | medium |
| `keyword_dependency:equip` | 25 | 25 | 9 | 25 | missing_contract | medium |
| `activated_effect:life-change` | 203 | 192 | 9 | 17 | missing_lowering | high |
| `keyword_dependency:split-second` | 21 | 21 | 8 | 21 | missing_contract | medium |
| `keyword_dependency:living-weapon` | 19 | 19 | 8 | 19 | missing_contract | medium |
| `keyword_dependency:extort` | 18 | 17 | 8 | 18 | missing_contract | medium |
| `keyword_dependency:umbra-armor` | 15 | 15 | 8 | 15 | missing_contract | medium |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:start-your-engines` | 3,635 | 7,815 | 7,829 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, activated_effect:create-token` | 3,634 | 7,818 | 7,834 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:banding` | 3,630 | 7,799 | 7,813 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, keyword_dependency:start-your-engines` | 3,629 | 7,792 | 7,794 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:create-token` | 3,628 | 7,858 | 7,872 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:equip` | 3,628 | 7,800 | 7,814 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:typed-spell-additional-cost-clause` | 3,628 | 7,793 | 7,895 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:life-change` | 3,626 | 7,819 | 7,833 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, activated_effect:put-onto-battlefield` | 3,626 | 7,807 | 7,822 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:myriad` | 3,626 | 7,798 | 7,812 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:start-your-engines` | 3,625 | 7,773 | 7,773 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:living-weapon` | 3,624 | 7,794 | 7,808 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, keyword_dependency:banding` | 3,624 | 7,776 | 7,778 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:exile` | 3,623 | 7,849 | 7,863 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, keyword_dependency:start-your-engines` | 3,623 | 7,832 | 7,832 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:umbra-armor` | 3,623 | 7,790 | 7,804 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:equip` | 3,623 | 7,774 | 7,774 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:start-your-engines` | 3,623 | 7,767 | 7,855 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, effect_clause:create-token` | 3,622 | 7,835 | 7,837 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, activated_effect:unparsed-investigate` | 3,622 | 7,788 | 7,802 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
