---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "0602f64acf817fd99145c845ac15bc02b39454658c0b4b1d4804ce56ebfcd579"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":13840,"partial":9600,"unresolved":8183}`
- CardProgram states: `{"residual":17783,"trusted":13840}`
- Hard construction failures: 0
- Frontier fingerprint: `0602f64acf817fd99145c845ac15bc02b39454658c0b4b1d4804ce56ebfcd579`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 3,447 | 2,890 | 1,600 | 3,447 | missing_lowering | very_high |
| `effect_clause:typed-spell-additional-cost-clause` | 102 | 102 | 20 | 20 | missing_lowering | high |
| `keyword_dependency:banding` | 24 | 24 | 19 | 24 | missing_contract | medium |
| `replacement:damage-prevention` | 126 | 124 | 14 | 26 | missing_lowering | very_high |
| `keyword_dependency:cipher` | 15 | 15 | 11 | 15 | missing_contract | medium |
| `activated_effect:create-token` | 238 | 233 | 10 | 34 | missing_lowering | high |
| `keyword_dependency:assist` | 16 | 16 | 9 | 16 | missing_contract | medium |
| `mechanic_dependency:morph-unsupported-cost` | 14 | 14 | 9 | 14 | missing_contract | high |
| `keyword_dependency:learn` | 13 | 13 | 9 | 13 | missing_contract | medium |
| `activated_effect:unparsed-you-get-an` | 69 | 69 | 8 | 67 | missing_lowering | high |
| `activated_effect:exile` | 265 | 250 | 8 | 20 | missing_lowering | high |
| `effect_clause:return` | 435 | 422 | 8 | 18 | missing_lowering | high |
| `keyword_dependency:fuse` | 34 | 17 | 7 | 34 | missing_contract | medium |
| `keyword_dependency:conspire` | 11 | 11 | 7 | 11 | missing_contract | medium |
| `activated_effect:unparsed-target-creature-can` | 15 | 15 | 7 | 10 | missing_lowering | high |
| `activated_effect:unparsed-this-creature-can` | 14 | 14 | 7 | 7 | missing_lowering | high |
| `effect_clause:create-token` | 454 | 441 | 6 | 55 | missing_lowering | high |
| `effect_clause:life-change` | 380 | 378 | 6 | 21 | missing_lowering | high |
| `effect_clause:destroy-mass` | 137 | 128 | 6 | 17 | missing_lowering | high |
| `keyword_dependency:phasing` | 12 | 12 | 6 | 12 | missing_contract | medium |
| `activated_effect:life-change` | 158 | 151 | 6 | 11 | missing_lowering | high |
| `keyword_dependency:provoke` | 8 | 8 | 6 | 8 | missing_contract | medium |
| `target_or_choice:typed-enchant-restriction` | 7 | 7 | 6 | 7 | lowered_untrusted | high |
| `effect_clause:unparsed-target-spell-or` | 7 | 7 | 6 | 6 | missing_lowering | high |
| `effect_clause:exile` | 480 | 472 | 5 | 52 | missing_lowering | high |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:banding` | 1,640 | 3,491 | 3,573 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:job-select` | 1,638 | 3,490 | 3,490 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:job-select` | 1,638 | 3,486 | 3,568 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, replacement:damage-prevention` | 1,634 | 3,497 | 3,497 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, replacement:damage-prevention` | 1,634 | 3,493 | 3,575 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, keyword_dependency:job-select` | 1,632 | 3,492 | 3,492 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:create-token` | 1,631 | 3,505 | 3,512 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:create-token` | 1,631 | 3,501 | 3,590 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:cipher` | 1,631 | 3,486 | 3,486 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:cipher` | 1,631 | 3,482 | 3,564 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:job-select` | 1,629 | 3,500 | 3,507 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:exile` | 1,629 | 3,491 | 3,493 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:exile` | 1,629 | 3,487 | 3,571 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:assist` | 1,629 | 3,487 | 3,487 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, mechanic_dependency:morph-unsupported-cost` | 1,629 | 3,485 | 3,485 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:learn` | 1,629 | 3,484 | 3,484 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:assist` | 1,629 | 3,483 | 3,565 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, mechanic_dependency:morph-unsupported-cost` | 1,629 | 3,481 | 3,563 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:cipher, keyword_dependency:job-select` | 1,629 | 3,481 | 3,481 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:learn` | 1,629 | 3,480 | 3,562 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
