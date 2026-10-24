---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "32f15522e868bf28bf16b432345c79818bbb30c721de0e4c2e3c1c9cf2209837"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":15320,"partial":8804,"unresolved":7499}`
- CardProgram states: `{"residual":16305,"trusted":15318}`
- Hard construction failures: 0
- Frontier fingerprint: `32f15522e868bf28bf16b432345c79818bbb30c721de0e4c2e3c1c9cf2209837`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 2,853 | 2,348 | 1,240 | 2,853 | missing_lowering | very_high |
| `keyword_dependency:banding` | 24 | 24 | 19 | 24 | missing_contract | medium |
| `effect_clause:typed-spell-additional-cost-clause` | 87 | 87 | 15 | 15 | missing_lowering | high |
| `replacement:damage-prevention` | 123 | 121 | 14 | 25 | missing_lowering | very_high |
| `keyword_dependency:cipher` | 15 | 15 | 11 | 15 | missing_contract | medium |
| `activated_effect:create-token` | 233 | 228 | 10 | 34 | missing_lowering | high |
| `keyword_dependency:fuse` | 34 | 17 | 9 | 34 | missing_contract | medium |
| `keyword_dependency:assist` | 16 | 16 | 9 | 16 | missing_contract | medium |
| `activated_effect:unparsed-you-get-an` | 69 | 69 | 8 | 67 | missing_lowering | high |
| `activated_effect:exile` | 260 | 246 | 8 | 20 | missing_lowering | high |
| `effect_clause:return` | 427 | 414 | 8 | 18 | missing_lowering | high |
| `activated_effect:unparsed-target-creature-can` | 15 | 15 | 7 | 10 | missing_lowering | high |
| `keyword_dependency:provoke` | 8 | 8 | 7 | 8 | missing_contract | medium |
| `effect_clause:create-token` | 424 | 413 | 6 | 49 | missing_lowering | high |
| `effect_clause:life-change` | 356 | 354 | 6 | 18 | missing_lowering | high |
| `effect_clause:destroy-mass` | 137 | 128 | 6 | 17 | missing_lowering | high |
| `effect_clause:counter` | 153 | 153 | 6 | 12 | missing_lowering | high |
| `keyword_dependency:phasing` | 12 | 12 | 6 | 12 | missing_contract | medium |
| `activated_effect:life-change` | 150 | 144 | 6 | 11 | missing_lowering | high |
| `effect_clause:unparsed-target-spell-or` | 7 | 7 | 6 | 6 | missing_lowering | high |
| `effect_clause:exile` | 476 | 469 | 5 | 50 | missing_lowering | high |
| `activated_effect:draw` | 218 | 217 | 5 | 6 | missing_lowering | high |
| `activated_effect:unparsed-the-next-time` | 15 | 15 | 5 | 6 | missing_lowering | high |
| `activated_effect:unparsed-target-creature-gets` | 14 | 14 | 5 | 6 | missing_lowering | high |
| `activated_effect:destroy-target` | 53 | 53 | 5 | 5 | missing_lowering | high |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:typed-spell-additional-cost-clause` | 1,275 | 2,892 | 2,964 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, replacement:damage-prevention` | 1,274 | 2,902 | 2,902 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:create-token` | 1,271 | 2,911 | 2,918 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:cipher` | 1,271 | 2,892 | 2,892 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:unparsed-you-get-an` | 1,269 | 2,944 | 2,946 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:fuse` | 1,269 | 2,911 | 2,911 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:exile` | 1,269 | 2,897 | 2,899 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, replacement:damage-prevention` | 1,269 | 2,893 | 2,965 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:assist` | 1,269 | 2,893 | 2,893 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:return` | 1,268 | 2,895 | 2,895 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:unparsed-target-creature-can` | 1,267 | 2,887 | 2,887 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:provoke` | 1,267 | 2,885 | 2,885 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:create-token` | 1,266 | 2,926 | 2,926 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:create-token` | 1,266 | 2,902 | 2,981 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:life-change` | 1,266 | 2,895 | 2,895 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:destroy-mass` | 1,266 | 2,894 | 2,894 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:counter` | 1,266 | 2,889 | 2,889 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:phasing` | 1,266 | 2,889 | 2,889 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:life-change` | 1,266 | 2,888 | 2,888 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:put-onto-battlefield` | 1,266 | 2,888 | 2,888 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
