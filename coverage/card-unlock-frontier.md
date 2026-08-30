---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "52b146df01c317655a839db7a1ea952159dfd1ca314abdaf2bc06e77e27180ab"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":8005,"partial":11634,"unresolved":11984}`
- CardProgram states: `{"residual":23618,"trusted":8005}`
- Hard construction failures: 0
- Frontier fingerprint: `52b146df01c317655a839db7a1ea952159dfd1ca314abdaf2bc06e77e27180ab`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 6,850 | 5,469 | 2,901 | 6,850 | missing_lowering | very_high |
| `replacement:damage-prevention` | 167 | 164 | 24 | 43 | missing_lowering | very_high |
| `activated_effect:create-token` | 314 | 307 | 22 | 66 | missing_lowering | high |
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
| `effect_clause:return` | 568 | 548 | 11 | 23 | missing_lowering | high |
| `effect_clause:unparsed-splice-onto-arcane` | 22 | 22 | 11 | 22 | missing_lowering | high |
| `effect_clause:unparsed-buyback-3` | 17 | 17 | 11 | 17 | missing_lowering | high |
| `activated_effect:unparsed-this-creature-can` | 20 | 20 | 11 | 12 | missing_lowering | high |
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
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, activated_effect:create-token` | 2,955 | 6,959 | 6,975 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:start-your-engines` | 2,953 | 6,956 | 6,970 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:banding` | 2,951 | 6,940 | 6,954 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:create-token` | 2,949 | 6,999 | 7,013 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:equip` | 2,949 | 6,941 | 6,955 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:typed-spell-additional-cost-clause` | 2,949 | 6,934 | 7,036 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:life-change` | 2,947 | 6,960 | 6,974 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, activated_effect:put-onto-battlefield` | 2,947 | 6,948 | 6,963 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:myriad` | 2,947 | 6,939 | 6,953 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, keyword_dependency:start-your-engines` | 2,947 | 6,933 | 6,935 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:living-weapon` | 2,945 | 6,935 | 6,949 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, keyword_dependency:banding` | 2,945 | 6,917 | 6,919 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:exile` | 2,944 | 6,990 | 7,004 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:umbra-armor` | 2,944 | 6,931 | 6,945 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, effect_clause:create-token` | 2,943 | 6,976 | 6,978 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, activated_effect:unparsed-investigate` | 2,943 | 6,929 | 6,943 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, keyword_dependency:equip` | 2,943 | 6,918 | 6,920 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:start-your-engines` | 2,943 | 6,914 | 6,914 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, effect_clause:typed-spell-additional-cost-clause` | 2,943 | 6,911 | 7,001 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:sacrifice` | 2,942 | 6,950 | 6,964 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
