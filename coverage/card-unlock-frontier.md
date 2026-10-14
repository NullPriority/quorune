---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "8fe60dc9d423d138604caaffd88bd8391698b6a74c71a64c7822d0ca03f4e29d"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":14259,"partial":9398,"unresolved":7966}`
- CardProgram states: `{"residual":17364,"trusted":14259}`
- Hard construction failures: 0
- Frontier fingerprint: `8fe60dc9d423d138604caaffd88bd8391698b6a74c71a64c7822d0ca03f4e29d`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 3,325 | 2,779 | 1,524 | 3,325 | missing_lowering | very_high |
| `effect_clause:typed-spell-additional-cost-clause` | 102 | 102 | 20 | 20 | missing_lowering | high |
| `keyword_dependency:banding` | 24 | 24 | 19 | 24 | missing_contract | medium |
| `replacement:damage-prevention` | 126 | 124 | 14 | 26 | missing_lowering | very_high |
| `keyword_dependency:cipher` | 15 | 15 | 11 | 15 | missing_contract | medium |
| `activated_effect:create-token` | 235 | 230 | 10 | 34 | missing_lowering | high |
| `mechanic_dependency:morph-unsupported-cost` | 14 | 14 | 10 | 14 | missing_contract | high |
| `keyword_dependency:assist` | 16 | 16 | 9 | 16 | missing_contract | medium |
| `keyword_dependency:learn` | 13 | 13 | 9 | 13 | missing_contract | medium |
| `activated_effect:unparsed-you-get-an` | 69 | 69 | 8 | 67 | missing_lowering | high |
| `keyword_dependency:fuse` | 34 | 17 | 8 | 34 | missing_contract | medium |
| `activated_effect:exile` | 264 | 249 | 8 | 20 | missing_lowering | high |
| `effect_clause:return` | 435 | 422 | 8 | 18 | missing_lowering | high |
| `keyword_dependency:conspire` | 11 | 11 | 7 | 11 | missing_contract | medium |
| `activated_effect:unparsed-target-creature-can` | 15 | 15 | 7 | 10 | missing_lowering | high |
| `activated_effect:unparsed-this-creature-can` | 14 | 14 | 7 | 7 | missing_lowering | high |
| `effect_clause:create-token` | 450 | 437 | 6 | 55 | missing_lowering | high |
| `effect_clause:life-change` | 377 | 375 | 6 | 21 | missing_lowering | high |
| `effect_clause:destroy-mass` | 137 | 128 | 6 | 17 | missing_lowering | high |
| `keyword_dependency:phasing` | 12 | 12 | 6 | 12 | missing_contract | medium |
| `activated_effect:life-change` | 156 | 149 | 6 | 11 | missing_lowering | high |
| `keyword_dependency:provoke` | 8 | 8 | 6 | 8 | missing_contract | medium |
| `target_or_choice:typed-enchant-restriction` | 7 | 7 | 6 | 7 | lowered_untrusted | high |
| `effect_clause:unparsed-target-spell-or` | 7 | 7 | 6 | 6 | missing_lowering | high |
| `effect_clause:exile` | 480 | 472 | 5 | 52 | missing_lowering | high |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:banding` | 1,564 | 3,369 | 3,451 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, replacement:damage-prevention` | 1,558 | 3,375 | 3,375 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, replacement:damage-prevention` | 1,558 | 3,371 | 3,453 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:create-token` | 1,555 | 3,383 | 3,390 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:create-token` | 1,555 | 3,379 | 3,468 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:cipher` | 1,555 | 3,364 | 3,364 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:cipher` | 1,555 | 3,360 | 3,442 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, mechanic_dependency:morph-unsupported-cost` | 1,554 | 3,363 | 3,363 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, mechanic_dependency:morph-unsupported-cost` | 1,554 | 3,359 | 3,441 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:unparsed-you-get-an` | 1,553 | 3,416 | 3,418 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:unparsed-you-get-an` | 1,553 | 3,412 | 3,496 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:exile` | 1,553 | 3,369 | 3,371 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:exile` | 1,553 | 3,365 | 3,449 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:assist` | 1,553 | 3,365 | 3,365 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:learn` | 1,553 | 3,362 | 3,362 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:assist` | 1,553 | 3,361 | 3,443 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:learn` | 1,553 | 3,358 | 3,440 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:fuse` | 1,552 | 3,383 | 3,383 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:fuse` | 1,552 | 3,379 | 3,461 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:return` | 1,552 | 3,367 | 3,367 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
