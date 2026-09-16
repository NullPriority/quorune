---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "30b9d88f5e970fe700ef3b3435dccbc8ba29568323e94424e7b000ece97b93ab"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":10182,"partial":11030,"unresolved":10411}`
- CardProgram states: `{"residual":21441,"trusted":10182}`
- Hard construction failures: 0
- Frontier fingerprint: `30b9d88f5e970fe700ef3b3435dccbc8ba29568323e94424e7b000ece97b93ab`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 4,813 | 4,026 | 2,096 | 4,813 | missing_lowering | very_high |
| `keyword_dependency:banding` | 24 | 24 | 19 | 24 | missing_contract | medium |
| `effect_clause:typed-spell-additional-cost-clause` | 106 | 106 | 18 | 18 | missing_lowering | high |
| `activated_effect:put-onto-battlefield` | 209 | 207 | 17 | 33 | missing_lowering | high |
| `effect_clause:life-change` | 471 | 469 | 16 | 41 | missing_lowering | high |
| `keyword_dependency:start-your-engines` | 40 | 40 | 14 | 40 | missing_contract | medium |
| `replacement:damage-prevention` | 140 | 138 | 14 | 28 | missing_lowering | very_high |
| `effect_clause:create-token` | 539 | 524 | 13 | 73 | missing_lowering | high |
| `activated_effect:create-token` | 283 | 276 | 12 | 48 | missing_lowering | high |
| `effect_clause:unparsed-splice-onto-arcane` | 22 | 22 | 12 | 22 | missing_lowering | high |
| `keyword_dependency:myriad` | 23 | 23 | 11 | 23 | missing_contract | medium |
| `activated_effect:unparsed-this-creature-can` | 20 | 20 | 11 | 12 | missing_lowering | high |
| `effect_clause:return` | 484 | 471 | 10 | 20 | missing_lowering | high |
| `keyword_dependency:umbra-armor` | 15 | 15 | 10 | 15 | missing_contract | medium |
| `keyword_dependency:extort` | 18 | 17 | 9 | 18 | missing_contract | medium |
| `activated_effect:life-change` | 180 | 170 | 9 | 17 | missing_lowering | high |
| `keyword_dependency:enlist` | 12 | 12 | 9 | 12 | missing_contract | medium |
| `keyword_dependency:split-second` | 21 | 21 | 8 | 21 | missing_contract | medium |
| `keyword_dependency:assist` | 16 | 16 | 8 | 16 | missing_contract | medium |
| `keyword_dependency:learn` | 13 | 13 | 8 | 13 | missing_contract | medium |
| `effect_clause:add-mana` | 57 | 57 | 8 | 11 | missing_lowering | high |
| `effect_clause:destroy-mass` | 141 | 132 | 7 | 18 | missing_lowering | high |
| `keyword_dependency:cipher` | 15 | 15 | 7 | 15 | missing_contract | medium |
| `activated_effect:unparsed-transform-this-creature` | 36 | 36 | 7 | 11 | missing_lowering | high |
| `activated_effect:unparsed-target-creature-can` | 15 | 15 | 7 | 10 | missing_lowering | high |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:start-your-engines` | 2,138 | 4,877 | 4,877 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:put-onto-battlefield, keyword_dependency:start-your-engines` | 2,137 | 4,886 | 4,886 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:create-token` | 2,136 | 4,901 | 4,910 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:start-your-engines` | 2,136 | 4,871 | 4,959 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:put-onto-battlefield` | 2,135 | 4,870 | 4,870 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:life-change, keyword_dependency:start-your-engines` | 2,134 | 4,894 | 4,894 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:create-token` | 2,134 | 4,885 | 4,894 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:myriad` | 2,134 | 4,876 | 4,876 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:typed-spell-additional-cost-clause` | 2,134 | 4,855 | 4,943 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:put-onto-battlefield, activated_effect:create-token` | 2,133 | 4,894 | 4,903 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:put-onto-battlefield` | 2,133 | 4,864 | 4,952 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, replacement:damage-prevention` | 2,132 | 4,881 | 4,883 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:create-token` | 2,132 | 4,879 | 4,976 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:life-change` | 2,132 | 4,878 | 4,878 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:myriad` | 2,132 | 4,860 | 4,860 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, effect_clause:create-token` | 2,131 | 4,926 | 4,926 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:put-onto-battlefield, effect_clause:life-change` | 2,131 | 4,887 | 4,887 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:put-onto-battlefield, keyword_dependency:myriad` | 2,131 | 4,869 | 4,869 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:umbra-armor` | 2,131 | 4,868 | 4,868 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:life-change, activated_effect:create-token` | 2,130 | 4,902 | 4,911 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
