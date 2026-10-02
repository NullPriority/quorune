---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "98a9a7994b4285c6f95551dc6aac953717408ee20dfeac2a745c12706c99a10d"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":11780,"partial":10482,"unresolved":9361}`
- CardProgram states: `{"residual":19843,"trusted":11780}`
- Hard construction failures: 0
- Frontier fingerprint: `98a9a7994b4285c6f95551dc6aac953717408ee20dfeac2a745c12706c99a10d`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 4,070 | 3,448 | 1,848 | 4,070 | missing_lowering | very_high |
| `effect_clause:typed-spell-additional-cost-clause` | 106 | 106 | 22 | 22 | missing_lowering | high |
| `keyword_dependency:banding` | 24 | 24 | 19 | 24 | missing_contract | medium |
| `keyword_dependency:start-your-engines` | 40 | 40 | 16 | 40 | missing_contract | medium |
| `replacement:damage-prevention` | 132 | 130 | 15 | 30 | missing_lowering | very_high |
| `activated_effect:create-token` | 266 | 259 | 13 | 39 | missing_lowering | high |
| `effect_clause:unparsed-splice-onto-arcane` | 22 | 22 | 13 | 22 | missing_lowering | high |
| `mechanic_dependency:fading-remaining-lifecycle` | 17 | 17 | 11 | 17 | missing_contract | high |
| `keyword_dependency:umbra-armor` | 15 | 15 | 11 | 15 | missing_contract | medium |
| `activated_effect:unparsed-this-creature-can` | 20 | 20 | 11 | 12 | missing_lowering | high |
| `keyword_dependency:extort` | 18 | 17 | 9 | 18 | missing_contract | medium |
| `keyword_dependency:assist` | 16 | 16 | 9 | 16 | missing_contract | medium |
| `activated_effect:life-change` | 175 | 167 | 9 | 14 | missing_lowering | high |
| `keyword_dependency:learn` | 13 | 13 | 9 | 13 | missing_contract | medium |
| `keyword_dependency:enlist` | 12 | 12 | 9 | 12 | missing_contract | medium |
| `effect_clause:create-token` | 510 | 495 | 8 | 63 | missing_lowering | high |
| `keyword_dependency:split-second` | 21 | 21 | 8 | 21 | missing_contract | medium |
| `activated_effect:exile` | 291 | 274 | 8 | 20 | missing_lowering | high |
| `effect_clause:return` | 476 | 463 | 8 | 18 | missing_lowering | high |
| `keyword_dependency:cipher` | 15 | 15 | 8 | 15 | missing_contract | medium |
| `mechanic_dependency:morph-unsupported-cost` | 14 | 14 | 8 | 14 | missing_contract | high |
| `mechanic_dependency:graft-remaining-lifecycle` | 13 | 13 | 8 | 13 | missing_contract | high |
| `effect_clause:add-mana` | 57 | 57 | 8 | 11 | missing_lowering | high |
| `effect_clause:exile` | 517 | 507 | 7 | 52 | missing_lowering | high |
| `keyword_dependency:fuse` | 34 | 17 | 7 | 34 | missing_contract | medium |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:start-your-engines` | 1,894 | 4,132 | 4,216 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:start-your-engines` | 1,892 | 4,134 | 4,134 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:banding` | 1,890 | 4,116 | 4,200 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, replacement:damage-prevention` | 1,887 | 4,140 | 4,140 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:create-token` | 1,886 | 4,149 | 4,156 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, effect_clause:unparsed-splice-onto-arcane` | 1,885 | 4,132 | 4,132 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:umbra-armor` | 1,885 | 4,125 | 4,125 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, replacement:damage-prevention` | 1,885 | 4,122 | 4,206 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:create-token` | 1,884 | 4,131 | 4,222 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:extort` | 1,883 | 4,128 | 4,128 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, mechanic_dependency:fading-remaining-lifecycle` | 1,883 | 4,127 | 4,127 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, replacement:damage-prevention` | 1,883 | 4,124 | 4,124 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:unparsed-this-creature-can` | 1,883 | 4,122 | 4,123 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, effect_clause:unparsed-splice-onto-arcane` | 1,883 | 4,114 | 4,198 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:umbra-armor` | 1,883 | 4,107 | 4,191 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:create-token` | 1,882 | 4,133 | 4,140 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:split-second` | 1,882 | 4,131 | 4,131 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:exile` | 1,881 | 4,130 | 4,132 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:assist` | 1,881 | 4,126 | 4,126 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:life-change` | 1,881 | 4,124 | 4,124 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
