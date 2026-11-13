---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "93636d9d1fadfc4d95179deae6df33c5bba14a00c87e237b0b16ac4b8a5c6361"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":18587,"partial":7073,"unresolved":5963}`
- CardProgram states: `{"residual":13038,"trusted":18574,"untrusted":11}`
- Hard construction failures: 0
- Frontier fingerprint: `93636d9d1fadfc4d95179deae6df33c5bba14a00c87e237b0b16ac4b8a5c6361`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 2,193 | 1,778 | 897 | 2,193 | missing_lowering | very_high |
| `effect_clause:create-token` | 765 | 753 | 36 | 89 | missing_lowering | high |
| `effect_clause:unparsed-when-this-creature` | 222 | 221 | 28 | 66 | missing_lowering | high |
| `effect_clause:return` | 791 | 776 | 28 | 55 | missing_lowering | high |
| `effect_clause:unparsed-whenever-this-creature` | 155 | 154 | 23 | 30 | missing_lowering | high |
| `effect_clause:deal-damage` | 794 | 774 | 20 | 61 | missing_lowering | high |
| `effect_clause:sacrifice` | 370 | 369 | 18 | 27 | missing_lowering | high |
| `effect_clause:exile` | 949 | 936 | 10 | 99 | missing_lowering | high |
| `effect_clause:life-change` | 607 | 600 | 10 | 31 | missing_lowering | high |
| `effect_clause:put-onto-battlefield` | 397 | 396 | 10 | 18 | missing_lowering | high |
| `activated_effect:unparsed-you-get-an` | 37 | 37 | 9 | 36 | missing_lowering | high |
| `effect_clause:unparsed-at-the-beginning` | 171 | 170 | 9 | 35 | missing_lowering | high |
| `effect_clause:typed-spell-additional-cost-clause` | 70 | 70 | 9 | 9 | missing_lowering | high |
| `effect_clause:destroy-mass` | 148 | 139 | 8 | 20 | missing_lowering | high |
| `replacement:damage-prevention` | 116 | 115 | 8 | 19 | missing_lowering | very_high |
| `effect_clause:draw` | 574 | 567 | 7 | 22 | missing_lowering | high |
| `effect_clause:look-reveal` | 557 | 556 | 7 | 8 | missing_lowering | high |
| `effect_clause:put-counter` | 637 | 632 | 6 | 26 | missing_lowering | high |
| `effect_clause:discard` | 214 | 212 | 6 | 11 | missing_lowering | high |
| `keyword_dependency:equip` | 8 | 8 | 6 | 8 | missing_contract | medium |
| `effect_clause:remove-counter` | 84 | 84 | 5 | 16 | missing_lowering | high |
| `activated_effect:draw` | 162 | 161 | 5 | 6 | missing_lowering | high |
| `activated_effect:life-change` | 122 | 118 | 5 | 6 | missing_lowering | high |
| `activated_effect:unparsed-the-next-time` | 15 | 15 | 5 | 6 | missing_lowering | high |
| `effect_clause:tap-state` | 374 | 368 | 4 | 19 | missing_lowering | high |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, effect_clause:unparsed-when-this-creature` | 992 | 2,348 | 2,348 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:return` | 983 | 2,314 | 2,314 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-whenever-this-creature` | 978 | 2,289 | 2,289 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:exile` | 976 | 2,358 | 2,358 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-at-the-beginning` | 975 | 2,294 | 2,294 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:deal-damage` | 974 | 2,320 | 2,320 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:sacrifice` | 970 | 2,286 | 2,286 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, effect_clause:return` | 968 | 2,337 | 2,337 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:life-change` | 964 | 2,290 | 2,290 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, effect_clause:unparsed-whenever-this-creature` | 963 | 2,312 | 2,312 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, activated_effect:unparsed-you-get-an` | 963 | 2,295 | 2,296 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:put-onto-battlefield` | 962 | 2,277 | 2,277 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, effect_clause:exile` | 961 | 2,381 | 2,381 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:typed-spell-additional-cost-clause` | 961 | 2,268 | 2,329 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, effect_clause:unparsed-at-the-beginning` | 960 | 2,317 | 2,317 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:put-counter` | 960 | 2,285 | 2,285 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:destroy-mass` | 960 | 2,279 | 2,279 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:tap-state` | 960 | 2,278 | 2,278 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, replacement:damage-prevention` | 960 | 2,278 | 2,278 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, effect_clause:deal-damage` | 959 | 2,343 | 2,343 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
