---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "e654183fdb4daa0ed8ac4db17d48808fd878c0c043cc1a92b1ed72a9078c27a9"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":16394,"partial":8188,"unresolved":7041}`
- CardProgram states: `{"residual":15231,"trusted":16362,"untrusted":30}`
- Hard construction failures: 0
- Frontier fingerprint: `e654183fdb4daa0ed8ac4db17d48808fd878c0c043cc1a92b1ed72a9078c27a9`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 2,410 | 1,944 | 954 | 2,410 | missing_lowering | very_high |
| `effect_clause:typed-spell-additional-cost-clause` | 72 | 72 | 11 | 11 | missing_lowering | high |
| `activated_effect:unparsed-you-get-an` | 69 | 69 | 10 | 67 | missing_lowering | high |
| `activated_effect:create-token` | 211 | 206 | 10 | 30 | missing_lowering | high |
| `keyword_dependency:assist` | 16 | 16 | 9 | 16 | missing_contract | medium |
| `effect_clause:life-change` | 347 | 345 | 6 | 18 | missing_lowering | high |
| `effect_clause:destroy-mass` | 137 | 128 | 6 | 17 | missing_lowering | high |
| `effect_clause:return` | 404 | 391 | 6 | 16 | missing_lowering | high |
| `keyword_dependency:phasing` | 12 | 12 | 6 | 12 | missing_contract | medium |
| `activated_effect:life-change` | 144 | 139 | 6 | 11 | missing_lowering | high |
| `effect_clause:unparsed-target-spell-or` | 7 | 7 | 6 | 6 | missing_lowering | high |
| `replacement:damage-prevention` | 113 | 111 | 5 | 16 | missing_lowering | very_high |
| `keyword_dependency:equip` | 8 | 8 | 5 | 8 | missing_contract | medium |
| `activated_effect:draw` | 198 | 197 | 5 | 6 | missing_lowering | high |
| `activated_effect:unparsed-the-next-time` | 15 | 15 | 5 | 6 | missing_lowering | high |
| `activated_effect:unparsed-target-creature-gets` | 13 | 13 | 5 | 6 | missing_lowering | high |
| `effect_clause:exile` | 460 | 453 | 4 | 48 | missing_lowering | high |
| `effect_clause:create-token` | 404 | 393 | 4 | 47 | missing_lowering | high |
| `activated_effect:exile` | 247 | 233 | 4 | 15 | missing_lowering | high |
| `activated_effect:put-onto-battlefield` | 161 | 159 | 4 | 11 | missing_lowering | high |
| `activated_effect:unparsed-the-next-spell` | 6 | 6 | 4 | 5 | missing_lowering | high |
| `activated_effect:unparsed-target-player-shuffles` | 5 | 5 | 4 | 5 | missing_lowering | high |
| `activated_effect:destroy-target` | 52 | 52 | 4 | 4 | missing_lowering | high |
| `effect_clause:unparsed-you-may-play` | 7 | 7 | 3 | 7 | missing_lowering | high |
| `activated_effect:return` | 192 | 192 | 3 | 5 | missing_lowering | high |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:unparsed-you-get-an, activated_effect:create-token` | 977 | 2,507 | 2,515 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:unparsed-you-get-an` | 976 | 2,488 | 2,551 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:create-token` | 976 | 2,451 | 2,518 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:unparsed-you-get-an, keyword_dependency:assist` | 974 | 2,493 | 2,495 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:assist` | 974 | 2,456 | 2,462 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:assist` | 974 | 2,437 | 2,498 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, effect_clause:ordered-effect-composition` | 973 | 2,809 | 2,810 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:life-change, effect_clause:ordered-effect-composition` | 972 | 2,776 | 2,776 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:unparsed-you-get-an, activated_effect:life-change` | 972 | 2,488 | 2,490 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:return, effect_clause:ordered-effect-composition` | 971 | 2,775 | 2,775 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:unparsed-you-get-an, effect_clause:life-change` | 971 | 2,495 | 2,497 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:unparsed-you-get-an, effect_clause:destroy-mass` | 971 | 2,494 | 2,496 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:unparsed-you-get-an, effect_clause:return` | 971 | 2,493 | 2,495 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:unparsed-you-get-an, keyword_dependency:phasing` | 971 | 2,489 | 2,491 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:unparsed-you-get-an, activated_effect:put-onto-battlefield` | 971 | 2,488 | 2,490 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:unparsed-you-get-an, keyword_dependency:equip` | 971 | 2,485 | 2,487 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:unparsed-you-get-an, effect_clause:unparsed-target-spell-or` | 971 | 2,483 | 2,485 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:life-change` | 971 | 2,458 | 2,464 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:destroy-mass` | 971 | 2,457 | 2,463 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:return` | 971 | 2,456 | 2,462 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
