---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "a9bd86484f476dd1f9d7c78d7fdfa46f96c8ba8a001b9281acf95b34902b072b"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":15860,"partial":8521,"unresolved":7242}`
- CardProgram states: `{"residual":15765,"trusted":15858}`
- Hard construction failures: 0
- Frontier fingerprint: `a9bd86484f476dd1f9d7c78d7fdfa46f96c8ba8a001b9281acf95b34902b072b`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 2,559 | 2,081 | 1,040 | 2,559 | missing_lowering | very_high |
| `keyword_dependency:cipher` | 15 | 15 | 11 | 15 | missing_contract | medium |
| `effect_clause:typed-spell-additional-cost-clause` | 72 | 72 | 10 | 10 | missing_lowering | high |
| `activated_effect:unparsed-you-get-an` | 69 | 69 | 9 | 67 | missing_lowering | high |
| `activated_effect:create-token` | 217 | 212 | 9 | 31 | missing_lowering | high |
| `keyword_dependency:assist` | 16 | 16 | 9 | 16 | missing_contract | medium |
| `effect_clause:life-change` | 350 | 348 | 6 | 18 | missing_lowering | high |
| `effect_clause:destroy-mass` | 137 | 128 | 6 | 17 | missing_lowering | high |
| `effect_clause:return` | 406 | 393 | 6 | 16 | missing_lowering | high |
| `keyword_dependency:phasing` | 12 | 12 | 6 | 12 | missing_contract | medium |
| `activated_effect:life-change` | 148 | 142 | 6 | 11 | missing_lowering | high |
| `effect_clause:unparsed-target-spell-or` | 7 | 7 | 6 | 6 | missing_lowering | high |
| `replacement:damage-prevention` | 114 | 112 | 5 | 16 | missing_lowering | very_high |
| `keyword_dependency:equip` | 8 | 8 | 5 | 8 | missing_contract | medium |
| `activated_effect:draw` | 211 | 210 | 5 | 6 | missing_lowering | high |
| `activated_effect:unparsed-the-next-time` | 15 | 15 | 5 | 6 | missing_lowering | high |
| `activated_effect:unparsed-target-creature-gets` | 14 | 14 | 5 | 6 | missing_lowering | high |
| `effect_clause:exile` | 460 | 453 | 4 | 48 | missing_lowering | high |
| `effect_clause:create-token` | 406 | 395 | 4 | 47 | missing_lowering | high |
| `activated_effect:exile` | 248 | 234 | 4 | 15 | missing_lowering | high |
| `activated_effect:put-onto-battlefield` | 161 | 159 | 4 | 11 | missing_lowering | high |
| `effect_clause:unparsed-all-creatures-able` | 6 | 6 | 4 | 6 | missing_lowering | high |
| `activated_effect:unparsed-the-next-spell` | 6 | 6 | 4 | 5 | missing_lowering | high |
| `activated_effect:unparsed-target-player-shuffles` | 5 | 5 | 4 | 5 | missing_lowering | high |
| `activated_effect:destroy-target` | 52 | 52 | 4 | 4 | missing_lowering | high |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:unparsed-you-get-an, activated_effect:create-token` | 1,061 | 2,657 | 2,665 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:cipher, activated_effect:unparsed-you-get-an` | 1,061 | 2,641 | 2,643 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:cipher, activated_effect:create-token` | 1,061 | 2,605 | 2,611 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:cipher, effect_clause:typed-spell-additional-cost-clause` | 1,061 | 2,584 | 2,646 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:unparsed-you-get-an` | 1,060 | 2,636 | 2,700 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:create-token` | 1,060 | 2,600 | 2,668 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:cipher, keyword_dependency:assist` | 1,060 | 2,590 | 2,590 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, effect_clause:ordered-effect-composition` | 1,059 | 2,974 | 2,975 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:unparsed-you-get-an, keyword_dependency:assist` | 1,059 | 2,642 | 2,644 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:assist` | 1,059 | 2,606 | 2,612 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:assist` | 1,059 | 2,585 | 2,647 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:life-change, effect_clause:ordered-effect-composition` | 1,058 | 2,941 | 2,941 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:return, effect_clause:ordered-effect-composition` | 1,057 | 2,940 | 2,940 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:unparsed-you-get-an, activated_effect:life-change` | 1,057 | 2,637 | 2,639 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:cipher, effect_clause:life-change` | 1,057 | 2,592 | 2,592 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:cipher, effect_clause:destroy-mass` | 1,057 | 2,591 | 2,591 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:cipher, effect_clause:return` | 1,057 | 2,590 | 2,590 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:cipher, keyword_dependency:phasing` | 1,057 | 2,586 | 2,586 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:cipher, activated_effect:life-change` | 1,057 | 2,585 | 2,585 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:cipher, activated_effect:put-onto-battlefield` | 1,057 | 2,585 | 2,585 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
