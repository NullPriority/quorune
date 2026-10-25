---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "a673df6c21dafe956f1485fe97a2ec113fbb01c524def5d83952430eed4c9701"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":15860,"partial":8517,"unresolved":7246}`
- CardProgram states: `{"residual":15765,"trusted":15858}`
- Hard construction failures: 0
- Frontier fingerprint: `a673df6c21dafe956f1485fe97a2ec113fbb01c524def5d83952430eed4c9701`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 2,565 | 2,081 | 1,041 | 2,565 | missing_lowering | very_high |
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
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:unparsed-you-get-an, activated_effect:create-token` | 1,062 | 2,663 | 2,671 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:cipher, activated_effect:unparsed-you-get-an` | 1,062 | 2,647 | 2,649 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:cipher, activated_effect:create-token` | 1,062 | 2,611 | 2,617 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:cipher, effect_clause:typed-spell-additional-cost-clause` | 1,062 | 2,590 | 2,652 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:unparsed-you-get-an` | 1,061 | 2,642 | 2,706 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, activated_effect:create-token` | 1,061 | 2,606 | 2,674 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:cipher, keyword_dependency:assist` | 1,061 | 2,596 | 2,596 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, effect_clause:ordered-effect-composition` | 1,060 | 2,980 | 2,981 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:unparsed-you-get-an, keyword_dependency:assist` | 1,060 | 2,648 | 2,650 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:assist` | 1,060 | 2,612 | 2,618 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:assist` | 1,060 | 2,591 | 2,653 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:life-change, effect_clause:ordered-effect-composition` | 1,059 | 2,947 | 2,947 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:return, effect_clause:ordered-effect-composition` | 1,058 | 2,946 | 2,946 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:unparsed-you-get-an, activated_effect:life-change` | 1,058 | 2,643 | 2,645 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:cipher, effect_clause:life-change` | 1,058 | 2,598 | 2,598 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:cipher, effect_clause:destroy-mass` | 1,058 | 2,597 | 2,597 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:cipher, effect_clause:return` | 1,058 | 2,596 | 2,596 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:cipher, keyword_dependency:phasing` | 1,058 | 2,592 | 2,592 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:cipher, activated_effect:life-change` | 1,058 | 2,591 | 2,591 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:cipher, activated_effect:put-onto-battlefield` | 1,058 | 2,591 | 2,591 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
