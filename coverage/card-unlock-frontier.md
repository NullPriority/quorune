---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "b04023c6b839a756e9aad198725dd99a3d5b9a67924dcfe1f7e57e81072c73e4"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":15112,"partial":8920,"unresolved":7591}`
- CardProgram states: `{"residual":16513,"trusted":15110}`
- Hard construction failures: 0
- Frontier fingerprint: `b04023c6b839a756e9aad198725dd99a3d5b9a67924dcfe1f7e57e81072c73e4`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 2,892 | 2,383 | 1,269 | 2,892 | missing_lowering | very_high |
| `keyword_dependency:banding` | 24 | 24 | 19 | 24 | missing_contract | medium |
| `effect_clause:typed-spell-additional-cost-clause` | 97 | 97 | 16 | 16 | missing_lowering | high |
| `replacement:damage-prevention` | 123 | 121 | 14 | 25 | missing_lowering | very_high |
| `keyword_dependency:cipher` | 15 | 15 | 11 | 15 | missing_contract | medium |
| `activated_effect:create-token` | 233 | 228 | 10 | 34 | missing_lowering | high |
| `keyword_dependency:assist` | 16 | 16 | 9 | 16 | missing_contract | medium |
| `activated_effect:unparsed-you-get-an` | 69 | 69 | 8 | 67 | missing_lowering | high |
| `keyword_dependency:fuse` | 34 | 17 | 8 | 34 | missing_contract | medium |
| `activated_effect:exile` | 260 | 246 | 8 | 20 | missing_lowering | high |
| `effect_clause:return` | 431 | 418 | 8 | 18 | missing_lowering | high |
| `activated_effect:unparsed-target-creature-can` | 15 | 15 | 7 | 10 | missing_lowering | high |
| `effect_clause:create-token` | 424 | 413 | 6 | 49 | missing_lowering | high |
| `effect_clause:life-change` | 358 | 356 | 6 | 18 | missing_lowering | high |
| `effect_clause:destroy-mass` | 137 | 128 | 6 | 17 | missing_lowering | high |
| `effect_clause:counter` | 154 | 154 | 6 | 12 | missing_lowering | high |
| `keyword_dependency:phasing` | 12 | 12 | 6 | 12 | missing_contract | medium |
| `activated_effect:life-change` | 151 | 145 | 6 | 11 | missing_lowering | high |
| `keyword_dependency:provoke` | 8 | 8 | 6 | 8 | missing_contract | medium |
| `target_or_choice:typed-enchant-restriction` | 7 | 7 | 6 | 7 | lowered_untrusted | high |
| `effect_clause:unparsed-target-spell-or` | 7 | 7 | 6 | 6 | missing_lowering | high |
| `effect_clause:exile` | 477 | 470 | 5 | 50 | missing_lowering | high |
| `keyword_dependency:increment` | 9 | 9 | 5 | 9 | missing_contract | medium |
| `activated_effect:unparsed-target-creature-gets` | 15 | 15 | 5 | 6 | missing_lowering | high |
| `activated_effect:destroy-target` | 54 | 54 | 5 | 5 | missing_lowering | high |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:typed-spell-additional-cost-clause` | 1,305 | 2,932 | 3,013 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, replacement:damage-prevention` | 1,303 | 2,941 | 2,941 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:create-token` | 1,300 | 2,950 | 2,957 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:cipher` | 1,300 | 2,931 | 2,931 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, replacement:damage-prevention` | 1,299 | 2,933 | 3,014 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:unparsed-you-get-an` | 1,298 | 2,983 | 2,985 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:exile` | 1,298 | 2,936 | 2,938 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:assist` | 1,298 | 2,932 | 2,932 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:fuse` | 1,297 | 2,950 | 2,950 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:return` | 1,297 | 2,934 | 2,934 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:create-token` | 1,296 | 2,942 | 3,030 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:unparsed-target-creature-can` | 1,296 | 2,926 | 2,926 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:cipher` | 1,296 | 2,923 | 3,004 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:create-token` | 1,295 | 2,965 | 2,965 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:life-change` | 1,295 | 2,934 | 2,934 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:destroy-mass` | 1,295 | 2,933 | 2,933 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:counter` | 1,295 | 2,928 | 2,928 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:phasing` | 1,295 | 2,928 | 2,928 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:life-change` | 1,295 | 2,927 | 2,927 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:put-onto-battlefield` | 1,295 | 2,927 | 2,927 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
