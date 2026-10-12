---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "54903dde0b98dffcf6a3882dd528a4aa94587763b4ebdcf7b9b3222e016003b1"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":13637,"partial":9708,"unresolved":8278}`
- CardProgram states: `{"residual":17986,"trusted":13637}`
- Hard construction failures: 0
- Frontier fingerprint: `54903dde0b98dffcf6a3882dd528a4aa94587763b4ebdcf7b9b3222e016003b1`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 3,484 | 2,924 | 1,628 | 3,484 | missing_lowering | very_high |
| `effect_clause:typed-spell-additional-cost-clause` | 102 | 102 | 20 | 20 | missing_lowering | high |
| `keyword_dependency:banding` | 24 | 24 | 19 | 24 | missing_contract | medium |
| `replacement:damage-prevention` | 126 | 124 | 14 | 26 | missing_lowering | very_high |
| `activated_effect:create-token` | 238 | 233 | 10 | 34 | missing_lowering | high |
| `keyword_dependency:cipher` | 15 | 15 | 10 | 15 | missing_contract | medium |
| `keyword_dependency:assist` | 16 | 16 | 9 | 16 | missing_contract | medium |
| `mechanic_dependency:morph-unsupported-cost` | 14 | 14 | 9 | 14 | missing_contract | high |
| `keyword_dependency:learn` | 13 | 13 | 9 | 13 | missing_contract | medium |
| `activated_effect:unparsed-you-get-an` | 69 | 69 | 8 | 67 | missing_lowering | high |
| `activated_effect:exile` | 265 | 250 | 8 | 20 | missing_lowering | high |
| `effect_clause:return` | 437 | 424 | 8 | 18 | missing_lowering | high |
| `keyword_dependency:fuse` | 34 | 17 | 7 | 34 | missing_contract | medium |
| `keyword_dependency:conspire` | 11 | 11 | 7 | 11 | missing_contract | medium |
| `activated_effect:unparsed-target-creature-can` | 15 | 15 | 7 | 10 | missing_lowering | high |
| `activated_effect:destroy-target` | 58 | 58 | 7 | 7 | missing_lowering | high |
| `activated_effect:unparsed-this-creature-can` | 14 | 14 | 7 | 7 | missing_lowering | high |
| `effect_clause:create-token` | 454 | 441 | 6 | 55 | missing_lowering | high |
| `effect_clause:life-change` | 382 | 380 | 6 | 22 | missing_lowering | high |
| `effect_clause:destroy-mass` | 137 | 128 | 6 | 17 | missing_lowering | high |
| `keyword_dependency:phasing` | 12 | 12 | 6 | 12 | missing_contract | medium |
| `activated_effect:life-change` | 158 | 151 | 6 | 11 | missing_lowering | high |
| `keyword_dependency:provoke` | 8 | 8 | 6 | 8 | missing_contract | medium |
| `target_or_choice:typed-enchant-restriction` | 7 | 7 | 6 | 7 | lowered_untrusted | high |
| `effect_clause:unparsed-target-spell-or` | 7 | 7 | 6 | 6 | missing_lowering | high |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:banding` | 1,668 | 3,528 | 3,610 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:job-select` | 1,666 | 3,527 | 3,527 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:job-select` | 1,666 | 3,523 | 3,605 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, replacement:damage-prevention` | 1,662 | 3,534 | 3,534 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, replacement:damage-prevention` | 1,662 | 3,530 | 3,612 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, keyword_dependency:job-select` | 1,660 | 3,529 | 3,529 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:create-token` | 1,659 | 3,542 | 3,549 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:create-token` | 1,659 | 3,538 | 3,627 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:cipher` | 1,658 | 3,523 | 3,523 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:cipher` | 1,658 | 3,519 | 3,601 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:job-select` | 1,657 | 3,537 | 3,544 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:exile` | 1,657 | 3,528 | 3,530 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:exile` | 1,657 | 3,524 | 3,608 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:assist` | 1,657 | 3,524 | 3,524 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, mechanic_dependency:morph-unsupported-cost` | 1,657 | 3,522 | 3,522 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:learn` | 1,657 | 3,521 | 3,521 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:assist` | 1,657 | 3,520 | 3,602 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, mechanic_dependency:morph-unsupported-cost` | 1,657 | 3,518 | 3,600 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:learn` | 1,657 | 3,517 | 3,599 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:unparsed-you-get-an` | 1,656 | 3,575 | 3,577 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
