---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "becafe88acde4edbd530632d9e267fed7e681f2491e820aaa38328c7ba5ea35c"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":18098,"partial":7341,"unresolved":6184}`
- CardProgram states: `{"residual":13527,"trusted":18085,"untrusted":11}`
- Hard construction failures: 0
- Frontier fingerprint: `becafe88acde4edbd530632d9e267fed7e681f2491e820aaa38328c7ba5ea35c`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 2,279 | 1,857 | 940 | 2,279 | missing_lowering | very_high |
| `effect_clause:unparsed-when-this-creature` | 254 | 253 | 40 | 89 | missing_lowering | high |
| `effect_clause:create-token` | 775 | 763 | 36 | 93 | missing_lowering | high |
| `effect_clause:unparsed-whenever-this-creature` | 163 | 162 | 30 | 38 | missing_lowering | high |
| `effect_clause:deal-damage` | 844 | 824 | 28 | 73 | missing_lowering | high |
| `effect_clause:return` | 842 | 827 | 26 | 56 | missing_lowering | high |
| `effect_clause:sacrifice` | 380 | 379 | 19 | 28 | missing_lowering | high |
| `effect_clause:life-change` | 646 | 639 | 11 | 32 | missing_lowering | high |
| `effect_clause:exile` | 966 | 953 | 10 | 99 | missing_lowering | high |
| `effect_clause:unparsed-at-the-beginning` | 177 | 176 | 10 | 37 | missing_lowering | high |
| `effect_clause:put-onto-battlefield` | 407 | 406 | 10 | 18 | missing_lowering | high |
| `activated_effect:unparsed-you-get-an` | 37 | 37 | 9 | 36 | missing_lowering | high |
| `effect_clause:typed-spell-additional-cost-clause` | 70 | 70 | 9 | 9 | missing_lowering | high |
| `effect_clause:destroy-mass` | 149 | 140 | 8 | 20 | missing_lowering | high |
| `replacement:damage-prevention` | 116 | 115 | 8 | 19 | missing_lowering | very_high |
| `effect_clause:put-counter` | 648 | 643 | 7 | 27 | missing_lowering | high |
| `effect_clause:draw` | 582 | 575 | 7 | 21 | missing_lowering | high |
| `effect_clause:look-reveal` | 559 | 558 | 7 | 8 | missing_lowering | high |
| `effect_clause:discard` | 214 | 212 | 6 | 11 | missing_lowering | high |
| `keyword_dependency:equip` | 8 | 8 | 6 | 8 | missing_contract | medium |
| `effect_clause:tap-state` | 374 | 368 | 5 | 20 | missing_lowering | high |
| `effect_clause:remove-counter` | 84 | 84 | 5 | 16 | missing_lowering | high |
| `activated_effect:life-change` | 128 | 123 | 5 | 7 | missing_lowering | high |
| `effect_clause:unparsed-whenever-a-creature` | 33 | 33 | 5 | 7 | missing_lowering | high |
| `activated_effect:draw` | 163 | 162 | 5 | 6 | missing_lowering | high |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:create-token` | 1,047 | 2,461 | 2,461 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-whenever-this-creature` | 1,041 | 2,406 | 2,406 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:deal-damage` | 1,040 | 2,441 | 2,441 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:return` | 1,038 | 2,424 | 2,424 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-at-the-beginning` | 1,033 | 2,405 | 2,405 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:exile` | 1,032 | 2,467 | 2,467 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:sacrifice` | 1,027 | 2,396 | 2,396 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:life-change` | 1,021 | 2,400 | 2,400 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, activated_effect:unparsed-you-get-an` | 1,019 | 2,404 | 2,405 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:put-onto-battlefield` | 1,018 | 2,386 | 2,386 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:put-counter` | 1,017 | 2,395 | 2,395 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:tap-state` | 1,017 | 2,388 | 2,388 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:typed-spell-additional-cost-clause` | 1,017 | 2,377 | 2,438 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:destroy-mass` | 1,016 | 2,388 | 2,388 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, replacement:damage-prevention` | 1,016 | 2,387 | 2,387 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:draw` | 1,015 | 2,389 | 2,389 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:discard` | 1,015 | 2,379 | 2,379 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:look-reveal` | 1,015 | 2,376 | 2,376 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-whenever-you-cast` | 1,015 | 2,376 | 2,376 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, keyword_dependency:equip` | 1,015 | 2,376 | 2,376 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
