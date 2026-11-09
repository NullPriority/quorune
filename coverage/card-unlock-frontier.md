---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "3f05fd8e3455ec522d3881504c78e2419f42296df1d0bf84e1b98d4f43ecae3c"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":17512,"partial":7671,"unresolved":6440}`
- CardProgram states: `{"residual":14113,"trusted":17500,"untrusted":10}`
- Hard construction failures: 0
- Frontier fingerprint: `3f05fd8e3455ec522d3881504c78e2419f42296df1d0bf84e1b98d4f43ecae3c`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 2,417 | 1,953 | 960 | 2,417 | missing_lowering | very_high |
| `effect_clause:unparsed-when-this-creature` | 259 | 258 | 40 | 89 | missing_lowering | high |
| `effect_clause:create-token` | 801 | 789 | 38 | 95 | missing_lowering | high |
| `effect_clause:unparsed-whenever-this-creature` | 164 | 163 | 31 | 39 | missing_lowering | high |
| `effect_clause:deal-damage` | 880 | 860 | 30 | 77 | missing_lowering | high |
| `effect_clause:return` | 874 | 859 | 24 | 55 | missing_lowering | high |
| `effect_clause:unparsed-at-the-beginning` | 218 | 208 | 12 | 43 | missing_lowering | high |
| `effect_clause:sacrifice` | 374 | 373 | 12 | 22 | missing_lowering | high |
| `effect_clause:life-change` | 651 | 645 | 11 | 32 | missing_lowering | high |
| `effect_clause:put-onto-battlefield` | 407 | 406 | 11 | 19 | missing_lowering | high |
| `effect_clause:exile` | 979 | 966 | 10 | 100 | missing_lowering | high |
| `activated_effect:unparsed-you-get-an` | 38 | 38 | 9 | 37 | missing_lowering | high |
| `effect_clause:put-counter` | 650 | 645 | 9 | 31 | missing_lowering | high |
| `effect_clause:typed-spell-additional-cost-clause` | 70 | 70 | 9 | 9 | missing_lowering | high |
| `activated_effect:create-token` | 188 | 183 | 8 | 26 | missing_lowering | high |
| `effect_clause:destroy-mass` | 149 | 140 | 8 | 20 | missing_lowering | high |
| `effect_clause:tap-state` | 400 | 394 | 7 | 24 | missing_lowering | high |
| `effect_clause:draw` | 600 | 593 | 7 | 21 | missing_lowering | high |
| `replacement:damage-prevention` | 118 | 117 | 7 | 18 | missing_lowering | very_high |
| `effect_clause:look-reveal` | 560 | 559 | 7 | 8 | missing_lowering | high |
| `effect_clause:unparsed-whenever-one-or` | 18 | 18 | 6 | 12 | missing_lowering | high |
| `effect_clause:discard` | 214 | 212 | 6 | 11 | missing_lowering | high |
| `keyword_dependency:equip` | 8 | 8 | 6 | 8 | missing_contract | medium |
| `effect_clause:unparsed-target-spell-or` | 7 | 7 | 6 | 6 | missing_lowering | high |
| `effect_clause:remove-counter` | 84 | 84 | 5 | 16 | missing_lowering | high |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:create-token` | 1,069 | 2,601 | 2,601 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:deal-damage` | 1,062 | 2,583 | 2,583 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-whenever-this-creature` | 1,062 | 2,545 | 2,545 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-at-the-beginning` | 1,057 | 2,549 | 2,549 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:return` | 1,056 | 2,561 | 2,561 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:exile` | 1,052 | 2,606 | 2,606 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:life-change` | 1,041 | 2,538 | 2,538 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:sacrifice` | 1,040 | 2,528 | 2,528 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, activated_effect:unparsed-you-get-an` | 1,039 | 2,543 | 2,544 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:put-counter` | 1,039 | 2,537 | 2,537 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:tap-state` | 1,039 | 2,530 | 2,530 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:put-onto-battlefield` | 1,039 | 2,525 | 2,525 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-whenever-one-or` | 1,038 | 2,518 | 2,518 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, activated_effect:create-token` | 1,037 | 2,532 | 2,534 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-whenever-you-cast` | 1,037 | 2,516 | 2,516 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:typed-spell-additional-cost-clause` | 1,037 | 2,515 | 2,576 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:destroy-mass` | 1,036 | 2,526 | 2,526 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, effect_clause:deal-damage` | 1,035 | 2,589 | 2,589 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, effect_clause:unparsed-whenever-this-creature` | 1,035 | 2,551 | 2,551 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:draw` | 1,035 | 2,527 | 2,527 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
