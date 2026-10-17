---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "8f204f7cd20993b9399ab7b134a46cd010167ad5b1ff4c17c44fc4351af5c3ca"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":14690,"partial":9176,"unresolved":7757}`
- CardProgram states: `{"residual":16935,"trusted":14688}`
- Hard construction failures: 0
- Frontier fingerprint: `8f204f7cd20993b9399ab7b134a46cd010167ad5b1ff4c17c44fc4351af5c3ca`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 3,248 | 2,707 | 1,492 | 3,248 | missing_lowering | very_high |
| `effect_clause:typed-spell-additional-cost-clause` | 102 | 102 | 20 | 20 | missing_lowering | high |
| `keyword_dependency:banding` | 24 | 24 | 19 | 24 | missing_contract | medium |
| `replacement:damage-prevention` | 124 | 122 | 14 | 26 | missing_lowering | very_high |
| `keyword_dependency:cipher` | 15 | 15 | 11 | 15 | missing_contract | medium |
| `activated_effect:create-token` | 233 | 228 | 10 | 34 | missing_lowering | high |
| `mechanic_dependency:morph-unsupported-cost` | 14 | 14 | 10 | 14 | missing_contract | high |
| `keyword_dependency:assist` | 16 | 16 | 9 | 16 | missing_contract | medium |
| `activated_effect:unparsed-you-get-an` | 69 | 69 | 8 | 67 | missing_lowering | high |
| `keyword_dependency:fuse` | 34 | 17 | 8 | 34 | missing_contract | medium |
| `activated_effect:exile` | 260 | 246 | 8 | 20 | missing_lowering | high |
| `effect_clause:return` | 434 | 421 | 8 | 18 | missing_lowering | high |
| `keyword_dependency:conspire` | 11 | 11 | 7 | 11 | missing_contract | medium |
| `activated_effect:unparsed-target-creature-can` | 15 | 15 | 7 | 10 | missing_lowering | high |
| `activated_effect:unparsed-this-creature-can` | 14 | 14 | 7 | 7 | missing_lowering | high |
| `effect_clause:create-token` | 429 | 416 | 6 | 52 | missing_lowering | high |
| `effect_clause:life-change` | 360 | 358 | 6 | 19 | missing_lowering | high |
| `effect_clause:destroy-mass` | 137 | 128 | 6 | 17 | missing_lowering | high |
| `keyword_dependency:phasing` | 12 | 12 | 6 | 12 | missing_contract | medium |
| `activated_effect:life-change` | 151 | 145 | 6 | 11 | missing_lowering | high |
| `keyword_dependency:provoke` | 8 | 8 | 6 | 8 | missing_contract | medium |
| `target_or_choice:typed-enchant-restriction` | 7 | 7 | 6 | 7 | lowered_untrusted | high |
| `effect_clause:unparsed-target-spell-or` | 7 | 7 | 6 | 6 | missing_lowering | high |
| `effect_clause:exile` | 477 | 470 | 5 | 50 | missing_lowering | high |
| `effect_clause:counter` | 154 | 154 | 5 | 12 | missing_lowering | high |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:banding` | 1,532 | 3,292 | 3,374 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, replacement:damage-prevention` | 1,526 | 3,298 | 3,298 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, replacement:damage-prevention` | 1,526 | 3,294 | 3,376 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:create-token` | 1,523 | 3,306 | 3,313 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:create-token` | 1,523 | 3,302 | 3,391 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:cipher` | 1,523 | 3,287 | 3,287 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:cipher` | 1,523 | 3,283 | 3,365 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, mechanic_dependency:morph-unsupported-cost` | 1,522 | 3,286 | 3,286 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, mechanic_dependency:morph-unsupported-cost` | 1,522 | 3,282 | 3,364 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:unparsed-you-get-an` | 1,521 | 3,339 | 3,341 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:unparsed-you-get-an` | 1,521 | 3,335 | 3,419 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:exile` | 1,521 | 3,292 | 3,294 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:exile` | 1,521 | 3,288 | 3,372 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:assist` | 1,521 | 3,288 | 3,288 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:assist` | 1,521 | 3,284 | 3,366 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:fuse` | 1,520 | 3,306 | 3,306 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:fuse` | 1,520 | 3,302 | 3,384 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:return` | 1,520 | 3,290 | 3,290 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, effect_clause:return` | 1,520 | 3,286 | 3,368 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:conspire` | 1,519 | 3,283 | 3,283 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
