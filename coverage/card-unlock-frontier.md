---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "a8576498579e1dd3b63473a9accfc98042ceee198c6e3e07c7b6e8d151733ebe"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":12064,"partial":10392,"unresolved":9167}`
- CardProgram states: `{"residual":19559,"trusted":12064}`
- Hard construction failures: 0
- Frontier fingerprint: `a8576498579e1dd3b63473a9accfc98042ceee198c6e3e07c7b6e8d151733ebe`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 4,047 | 3,438 | 1,866 | 4,047 | missing_lowering | very_high |
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
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:start-your-engines` | 1,912 | 4,109 | 4,193 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:start-your-engines` | 1,910 | 4,111 | 4,111 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:banding` | 1,908 | 4,093 | 4,177 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, replacement:damage-prevention` | 1,905 | 4,117 | 4,117 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:create-token` | 1,904 | 4,126 | 4,133 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, effect_clause:unparsed-splice-onto-arcane` | 1,903 | 4,109 | 4,109 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:umbra-armor` | 1,903 | 4,102 | 4,102 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, replacement:damage-prevention` | 1,903 | 4,099 | 4,183 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:create-token` | 1,902 | 4,108 | 4,199 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:extort` | 1,902 | 4,105 | 4,105 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, mechanic_dependency:fading-remaining-lifecycle` | 1,901 | 4,104 | 4,104 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, replacement:damage-prevention` | 1,901 | 4,101 | 4,101 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:unparsed-this-creature-can` | 1,901 | 4,099 | 4,100 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, effect_clause:unparsed-splice-onto-arcane` | 1,901 | 4,091 | 4,175 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:umbra-armor` | 1,901 | 4,084 | 4,168 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:create-token` | 1,900 | 4,110 | 4,117 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:split-second` | 1,900 | 4,108 | 4,108 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:extort` | 1,900 | 4,087 | 4,171 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:exile` | 1,899 | 4,107 | 4,109 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:assist` | 1,899 | 4,103 | 4,103 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
