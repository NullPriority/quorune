---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "6376d490ef8a9918dadcb02a4bec07af6c6ea9cd35118cb617f6bcb53a3f6237"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":12012,"partial":10406,"unresolved":9205}`
- CardProgram states: `{"residual":19611,"trusted":12012}`
- Hard construction failures: 0
- Frontier fingerprint: `6376d490ef8a9918dadcb02a4bec07af6c6ea9cd35118cb617f6bcb53a3f6237`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 4,052 | 3,441 | 1,862 | 4,052 | missing_lowering | very_high |
| `effect_clause:typed-spell-additional-cost-clause` | 106 | 106 | 22 | 22 | missing_lowering | high |
| `keyword_dependency:banding` | 24 | 24 | 19 | 24 | missing_contract | medium |
| `keyword_dependency:start-your-engines` | 40 | 40 | 16 | 40 | missing_contract | medium |
| `replacement:damage-prevention` | 132 | 130 | 15 | 30 | missing_lowering | very_high |
| `activated_effect:create-token` | 266 | 259 | 13 | 39 | missing_lowering | high |
| `effect_clause:unparsed-splice-onto-arcane` | 22 | 22 | 13 | 22 | missing_lowering | high |
| `mechanic_dependency:fading-remaining-lifecycle` | 17 | 17 | 11 | 17 | missing_contract | high |
| `keyword_dependency:umbra-armor` | 15 | 15 | 11 | 15 | missing_contract | medium |
| `activated_effect:unparsed-this-creature-can` | 20 | 20 | 11 | 12 | missing_lowering | high |
| `keyword_dependency:extort` | 18 | 17 | 10 | 18 | missing_contract | medium |
| `keyword_dependency:assist` | 16 | 16 | 9 | 16 | missing_contract | medium |
| `keyword_dependency:cipher` | 15 | 15 | 9 | 15 | missing_contract | medium |
| `activated_effect:life-change` | 171 | 163 | 9 | 14 | missing_lowering | high |
| `keyword_dependency:learn` | 13 | 13 | 9 | 13 | missing_contract | medium |
| `keyword_dependency:enlist` | 12 | 12 | 9 | 12 | missing_contract | medium |
| `effect_clause:create-token` | 509 | 494 | 8 | 63 | missing_lowering | high |
| `keyword_dependency:aftermath` | 27 | 27 | 8 | 27 | missing_contract | medium |
| `keyword_dependency:split-second` | 21 | 21 | 8 | 21 | missing_contract | medium |
| `activated_effect:exile` | 269 | 253 | 8 | 20 | missing_lowering | high |
| `effect_clause:return` | 447 | 434 | 8 | 18 | missing_lowering | high |
| `mechanic_dependency:morph-unsupported-cost` | 14 | 14 | 8 | 14 | missing_contract | high |
| `mechanic_dependency:graft-remaining-lifecycle` | 13 | 13 | 8 | 13 | missing_contract | high |
| `effect_clause:add-mana` | 57 | 57 | 8 | 11 | missing_lowering | high |
| `effect_clause:exile` | 486 | 478 | 7 | 52 | missing_lowering | high |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:start-your-engines` | 1,908 | 4,114 | 4,198 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:start-your-engines` | 1,906 | 4,116 | 4,116 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:banding` | 1,904 | 4,098 | 4,182 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, replacement:damage-prevention` | 1,901 | 4,122 | 4,122 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:create-token` | 1,900 | 4,131 | 4,138 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, effect_clause:unparsed-splice-onto-arcane` | 1,899 | 4,114 | 4,114 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:umbra-armor` | 1,899 | 4,107 | 4,107 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, replacement:damage-prevention` | 1,899 | 4,104 | 4,188 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:create-token` | 1,898 | 4,113 | 4,204 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:extort` | 1,898 | 4,110 | 4,110 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, mechanic_dependency:fading-remaining-lifecycle` | 1,897 | 4,109 | 4,109 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, replacement:damage-prevention` | 1,897 | 4,106 | 4,106 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:unparsed-this-creature-can` | 1,897 | 4,104 | 4,105 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, effect_clause:unparsed-splice-onto-arcane` | 1,897 | 4,096 | 4,180 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:umbra-armor` | 1,897 | 4,089 | 4,173 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:create-token` | 1,896 | 4,115 | 4,122 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:split-second` | 1,896 | 4,113 | 4,113 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:extort` | 1,896 | 4,092 | 4,176 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:exile` | 1,895 | 4,112 | 4,114 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:assist` | 1,895 | 4,108 | 4,108 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
