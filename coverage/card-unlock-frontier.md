---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "79596de76ec0df9b7d70b153487780916b5cb820fb9ba680e4e5b18386179a2d"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":10132,"partial":11030,"unresolved":10461}`
- CardProgram states: `{"residual":21491,"trusted":10132}`
- Hard construction failures: 0
- Frontier fingerprint: `79596de76ec0df9b7d70b153487780916b5cb820fb9ba680e4e5b18386179a2d`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 4,949 | 4,152 | 2,146 | 4,949 | missing_lowering | very_high |
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
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:start-your-engines` | 2,188 | 5,013 | 5,013 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:put-onto-battlefield, keyword_dependency:start-your-engines` | 2,187 | 5,022 | 5,022 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:create-token` | 2,186 | 5,037 | 5,046 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:start-your-engines` | 2,186 | 5,007 | 5,095 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:put-onto-battlefield` | 2,185 | 5,006 | 5,006 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:life-change, keyword_dependency:start-your-engines` | 2,184 | 5,030 | 5,030 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:create-token` | 2,184 | 5,021 | 5,030 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:myriad` | 2,184 | 5,012 | 5,012 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:typed-spell-additional-cost-clause` | 2,184 | 4,991 | 5,079 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:put-onto-battlefield, activated_effect:create-token` | 2,183 | 5,030 | 5,039 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:put-onto-battlefield` | 2,183 | 5,000 | 5,088 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, replacement:damage-prevention` | 2,182 | 5,017 | 5,019 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:create-token` | 2,182 | 5,015 | 5,112 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:life-change` | 2,182 | 5,014 | 5,014 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:myriad` | 2,182 | 4,996 | 4,996 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, effect_clause:create-token` | 2,181 | 5,062 | 5,062 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:put-onto-battlefield, effect_clause:life-change` | 2,181 | 5,023 | 5,023 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:put-onto-battlefield, keyword_dependency:myriad` | 2,181 | 5,005 | 5,005 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:umbra-armor` | 2,181 | 5,004 | 5,004 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:life-change, activated_effect:create-token` | 2,180 | 5,038 | 5,047 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
