---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "2fb53dc74fc7f90a3642c36300e84985f5133294523b399567957613bead213f"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":16994,"partial":7909,"unresolved":6720}`
- CardProgram states: `{"residual":14631,"trusted":16968,"untrusted":24}`
- Hard construction failures: 0
- Frontier fingerprint: `2fb53dc74fc7f90a3642c36300e84985f5133294523b399567957613bead213f`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 2,440 | 1,970 | 959 | 2,440 | missing_lowering | very_high |
| `effect_clause:create-token` | 916 | 902 | 72 | 146 | missing_lowering | high |
| `effect_clause:unparsed-when-this-creature` | 291 | 289 | 54 | 108 | missing_lowering | high |
| `effect_clause:unparsed-whenever-this-creature` | 173 | 172 | 35 | 45 | missing_lowering | high |
| `effect_clause:deal-damage` | 942 | 922 | 34 | 83 | missing_lowering | high |
| `effect_clause:return` | 878 | 863 | 24 | 55 | missing_lowering | high |
| `effect_clause:unparsed-at-the-beginning` | 222 | 212 | 13 | 46 | missing_lowering | high |
| `effect_clause:life-change` | 685 | 679 | 13 | 35 | missing_lowering | high |
| `effect_clause:tap-state` | 419 | 413 | 13 | 30 | missing_lowering | high |
| `effect_clause:sacrifice` | 376 | 375 | 12 | 22 | missing_lowering | high |
| `effect_clause:put-onto-battlefield` | 422 | 421 | 11 | 19 | missing_lowering | high |
| `effect_clause:typed-spell-additional-cost-clause` | 72 | 72 | 11 | 11 | missing_lowering | high |
| `effect_clause:exile` | 999 | 986 | 10 | 104 | missing_lowering | high |
| `activated_effect:unparsed-you-get-an` | 40 | 40 | 9 | 39 | missing_lowering | high |
| `effect_clause:put-counter` | 663 | 658 | 9 | 31 | missing_lowering | high |
| `effect_clause:draw` | 633 | 626 | 9 | 23 | missing_lowering | high |
| `keyword_dependency:assist` | 16 | 16 | 9 | 16 | missing_contract | medium |
| `activated_effect:create-token` | 199 | 194 | 8 | 28 | missing_lowering | high |
| `effect_clause:destroy-mass` | 165 | 156 | 8 | 20 | missing_lowering | high |
| `replacement:damage-prevention` | 119 | 118 | 7 | 18 | missing_lowering | very_high |
| `effect_clause:look-reveal` | 570 | 569 | 7 | 8 | missing_lowering | high |
| `effect_clause:unparsed-whenever-you-cast` | 38 | 38 | 6 | 12 | missing_lowering | high |
| `effect_clause:unparsed-whenever-one-or` | 18 | 18 | 6 | 12 | missing_lowering | high |
| `keyword_dependency:phasing` | 12 | 12 | 6 | 12 | missing_contract | medium |
| `keyword_dependency:equip` | 8 | 8 | 6 | 8 | missing_contract | medium |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, effect_clause:unparsed-when-this-creature` | 1,117 | 2,694 | 2,694 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-whenever-this-creature` | 1,080 | 2,593 | 2,593 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:deal-damage` | 1,079 | 2,631 | 2,631 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, effect_clause:unparsed-whenever-this-creature` | 1,073 | 2,631 | 2,631 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, effect_clause:deal-damage` | 1,072 | 2,669 | 2,669 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-at-the-beginning` | 1,072 | 2,594 | 2,594 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:return` | 1,069 | 2,603 | 2,603 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:exile` | 1,065 | 2,652 | 2,652 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, effect_clause:unparsed-at-the-beginning` | 1,065 | 2,632 | 2,632 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, effect_clause:return` | 1,062 | 2,641 | 2,641 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, effect_clause:exile` | 1,058 | 2,690 | 2,690 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:tap-state` | 1,058 | 2,578 | 2,578 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:life-change` | 1,056 | 2,583 | 2,583 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:sacrifice` | 1,053 | 2,570 | 2,570 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, activated_effect:unparsed-you-get-an` | 1,052 | 2,587 | 2,588 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:put-counter` | 1,052 | 2,579 | 2,579 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:put-onto-battlefield` | 1,052 | 2,567 | 2,567 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-whenever-you-cast` | 1,052 | 2,560 | 2,560 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:typed-spell-additional-cost-clause` | 1,052 | 2,559 | 2,620 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, effect_clause:tap-state` | 1,051 | 2,616 | 2,616 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
