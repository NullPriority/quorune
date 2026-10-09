---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "f8f4ae369c84151f6209fc4d807631efdce0f64d014f1e1470e14cd82629d9ba"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":12492,"partial":10309,"unresolved":8822}`
- CardProgram states: `{"residual":19131,"trusted":12492}`
- Hard construction failures: 0
- Frontier fingerprint: `f8f4ae369c84151f6209fc4d807631efdce0f64d014f1e1470e14cd82629d9ba`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 3,884 | 3,286 | 1,840 | 3,884 | missing_lowering | very_high |
| `effect_clause:typed-spell-additional-cost-clause` | 106 | 106 | 22 | 22 | missing_lowering | high |
| `keyword_dependency:banding` | 24 | 24 | 19 | 24 | missing_contract | medium |
| `replacement:damage-prevention` | 131 | 129 | 18 | 30 | missing_lowering | very_high |
| `keyword_dependency:start-your-engines` | 40 | 40 | 16 | 40 | missing_contract | medium |
| `effect_clause:unparsed-splice-onto-arcane` | 22 | 22 | 13 | 22 | missing_lowering | high |
| `mechanic_dependency:fading-remaining-lifecycle` | 17 | 17 | 11 | 17 | missing_contract | high |
| `keyword_dependency:umbra-armor` | 15 | 15 | 11 | 15 | missing_contract | medium |
| `activated_effect:unparsed-this-creature-can` | 20 | 20 | 11 | 12 | missing_lowering | high |
| `activated_effect:create-token` | 245 | 240 | 10 | 35 | missing_lowering | high |
| `keyword_dependency:extort` | 18 | 17 | 10 | 18 | missing_contract | medium |
| `keyword_dependency:cipher` | 15 | 15 | 10 | 15 | missing_contract | medium |
| `keyword_dependency:split-second` | 21 | 21 | 9 | 21 | missing_contract | medium |
| `keyword_dependency:assist` | 16 | 16 | 9 | 16 | missing_contract | medium |
| `activated_effect:life-change` | 167 | 160 | 9 | 14 | missing_lowering | high |
| `keyword_dependency:learn` | 13 | 13 | 9 | 13 | missing_contract | medium |
| `mechanic_dependency:graft-remaining-lifecycle` | 13 | 13 | 9 | 13 | missing_contract | high |
| `keyword_dependency:enlist` | 12 | 12 | 9 | 12 | missing_contract | medium |
| `effect_clause:create-token` | 460 | 447 | 8 | 56 | missing_lowering | high |
| `keyword_dependency:aftermath` | 27 | 27 | 8 | 27 | missing_contract | medium |
| `activated_effect:exile` | 266 | 250 | 8 | 20 | missing_lowering | high |
| `effect_clause:return` | 442 | 429 | 8 | 18 | missing_lowering | high |
| `mechanic_dependency:morph-unsupported-cost` | 14 | 14 | 8 | 14 | missing_contract | high |
| `effect_clause:add-mana` | 57 | 57 | 8 | 11 | missing_lowering | high |
| `activated_effect:unparsed-you-get-an` | 69 | 69 | 7 | 67 | missing_lowering | high |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:start-your-engines` | 1,886 | 3,946 | 4,030 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:start-your-engines` | 1,884 | 3,948 | 3,948 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, keyword_dependency:start-your-engines` | 1,882 | 3,954 | 3,954 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:banding` | 1,882 | 3,930 | 4,014 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, replacement:damage-prevention` | 1,880 | 3,936 | 4,020 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, replacement:damage-prevention` | 1,878 | 3,938 | 3,938 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, effect_clause:unparsed-splice-onto-arcane` | 1,877 | 3,946 | 3,946 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:umbra-armor` | 1,877 | 3,939 | 3,939 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:extort` | 1,876 | 3,942 | 3,942 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:create-token` | 1,875 | 3,959 | 3,966 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:split-second` | 1,875 | 3,945 | 3,945 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, mechanic_dependency:fading-remaining-lifecycle` | 1,875 | 3,941 | 3,941 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:unparsed-this-creature-can` | 1,875 | 3,936 | 3,937 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, effect_clause:unparsed-splice-onto-arcane` | 1,875 | 3,928 | 4,012 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:umbra-armor` | 1,875 | 3,921 | 4,005 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:cipher` | 1,874 | 3,939 | 3,939 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:extort` | 1,874 | 3,924 | 4,008 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:exile` | 1,873 | 3,944 | 3,946 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:create-token` | 1,873 | 3,941 | 4,032 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:assist` | 1,873 | 3,940 | 3,940 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
