---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "be2d716b3dc4e07aa8b518436279e5db7be6a6b31ded94cfb96e8bc457cbf450"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":12913,"partial":10056,"unresolved":8654}`
- CardProgram states: `{"residual":18710,"trusted":12913}`
- Hard construction failures: 0
- Frontier fingerprint: `be2d716b3dc4e07aa8b518436279e5db7be6a6b31ded94cfb96e8bc457cbf450`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 3,772 | 3,187 | 1,791 | 3,772 | missing_lowering | very_high |
| `effect_clause:typed-spell-additional-cost-clause` | 106 | 106 | 22 | 22 | missing_lowering | high |
| `keyword_dependency:banding` | 24 | 24 | 19 | 24 | missing_contract | medium |
| `replacement:damage-prevention` | 131 | 129 | 18 | 30 | missing_lowering | very_high |
| `keyword_dependency:start-your-engines` | 40 | 40 | 16 | 40 | missing_contract | medium |
| `activated_effect:create-token` | 243 | 238 | 10 | 34 | missing_lowering | high |
| `keyword_dependency:extort` | 18 | 17 | 10 | 18 | missing_contract | medium |
| `keyword_dependency:cipher` | 15 | 15 | 10 | 15 | missing_contract | medium |
| `keyword_dependency:split-second` | 21 | 21 | 9 | 21 | missing_contract | medium |
| `keyword_dependency:assist` | 16 | 16 | 9 | 16 | missing_contract | medium |
| `activated_effect:life-change` | 167 | 160 | 9 | 14 | missing_lowering | high |
| `mechanic_dependency:morph-unsupported-cost` | 14 | 14 | 9 | 14 | missing_contract | high |
| `keyword_dependency:learn` | 13 | 13 | 9 | 13 | missing_contract | medium |
| `mechanic_dependency:graft-remaining-lifecycle` | 13 | 13 | 9 | 13 | missing_contract | high |
| `keyword_dependency:enlist` | 12 | 12 | 9 | 12 | missing_contract | medium |
| `activated_effect:unparsed-you-get-an` | 69 | 69 | 8 | 67 | missing_lowering | high |
| `keyword_dependency:aftermath` | 27 | 27 | 8 | 27 | missing_contract | medium |
| `activated_effect:exile` | 266 | 250 | 8 | 20 | missing_lowering | high |
| `effect_clause:return` | 438 | 425 | 8 | 18 | missing_lowering | high |
| `keyword_dependency:fuse` | 34 | 17 | 7 | 34 | missing_contract | medium |
| `activated_effect:unparsed-transform-this-creature` | 36 | 36 | 7 | 12 | missing_lowering | high |
| `effect_clause:add-mana` | 53 | 53 | 7 | 11 | missing_lowering | high |
| `activated_effect:unparsed-target-creature-can` | 15 | 15 | 7 | 10 | missing_lowering | high |
| `activated_effect:destroy-target` | 58 | 58 | 7 | 7 | missing_lowering | high |
| `activated_effect:unparsed-this-creature-can` | 14 | 14 | 7 | 7 | missing_lowering | high |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:start-your-engines` | 1,837 | 3,834 | 3,918 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:start-your-engines` | 1,835 | 3,836 | 3,836 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, keyword_dependency:start-your-engines` | 1,833 | 3,842 | 3,842 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:banding` | 1,833 | 3,818 | 3,902 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, replacement:damage-prevention` | 1,831 | 3,824 | 3,908 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, replacement:damage-prevention` | 1,829 | 3,826 | 3,826 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:extort` | 1,827 | 3,830 | 3,830 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:create-token` | 1,826 | 3,846 | 3,853 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:split-second` | 1,826 | 3,833 | 3,833 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:cipher` | 1,825 | 3,827 | 3,827 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:extort` | 1,825 | 3,812 | 3,896 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:exile` | 1,824 | 3,832 | 3,834 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:create-token` | 1,824 | 3,828 | 3,919 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:assist` | 1,824 | 3,828 | 3,828 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:life-change` | 1,824 | 3,826 | 3,826 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, mechanic_dependency:morph-unsupported-cost` | 1,824 | 3,826 | 3,826 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:learn` | 1,824 | 3,825 | 3,825 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, mechanic_dependency:graft-remaining-lifecycle` | 1,824 | 3,825 | 3,825 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:enlist` | 1,824 | 3,824 | 3,824 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:split-second` | 1,824 | 3,815 | 3,899 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
