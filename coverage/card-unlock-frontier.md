---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "f8035ca2bd9328d3af7857204651092e0d3dd5a49709cf872cbb2e82c09bc861"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":20150,"partial":6279,"unresolved":5194}`
- CardProgram states: `{"residual":11473,"trusted":20146,"untrusted":4}`
- Hard construction failures: 0
- Frontier fingerprint: `f8035ca2bd9328d3af7857204651092e0d3dd5a49709cf872cbb2e82c09bc861`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 1,934 | 1,536 | 784 | 1,934 | missing_lowering | very_high |
| `effect_clause:create-token` | 662 | 652 | 17 | 59 | missing_lowering | high |
| `effect_clause:unparsed-when-this-creature` | 193 | 192 | 15 | 52 | missing_lowering | high |
| `effect_clause:return` | 713 | 700 | 11 | 36 | missing_lowering | high |
| `effect_clause:exile` | 881 | 868 | 10 | 97 | missing_lowering | high |
| `effect_clause:put-onto-battlefield` | 366 | 365 | 9 | 15 | missing_lowering | high |
| `effect_clause:unparsed-whenever-this-creature` | 134 | 133 | 9 | 14 | missing_lowering | high |
| `effect_clause:deal-damage` | 691 | 672 | 7 | 41 | missing_lowering | high |
| `effect_clause:unparsed-at-the-beginning` | 157 | 157 | 6 | 32 | missing_lowering | high |
| `activated_effect:unparsed-you-get-an` | 30 | 30 | 6 | 29 | missing_lowering | high |
| `replacement:damage-prevention` | 99 | 98 | 6 | 13 | missing_lowering | very_high |
| `effect_clause:put-counter` | 605 | 601 | 5 | 23 | missing_lowering | high |
| `activated_effect:unparsed-the-next-time` | 15 | 15 | 5 | 6 | missing_lowering | high |
| `effect_clause:life-change` | 513 | 506 | 4 | 19 | missing_lowering | high |
| `activated_effect:exile` | 211 | 198 | 4 | 15 | missing_lowering | high |
| `effect_clause:typed-spell-additional-cost-clause` | 61 | 61 | 4 | 4 | missing_lowering | high |
| `activated_effect:create-token` | 152 | 147 | 3 | 17 | missing_lowering | high |
| `effect_clause:unparsed-whenever-you-cast` | 27 | 27 | 3 | 8 | missing_lowering | high |
| `activated_effect:deal-damage` | 175 | 167 | 3 | 5 | missing_lowering | high |
| `effect_clause:variable-nonmana-repeated-granted-instances-and-permanent-spells` | 5 | 5 | 3 | 5 | missing_lowering | high |
| `effect_clause:look-reveal` | 472 | 471 | 3 | 4 | missing_lowering | high |
| `activated_effect:return` | 163 | 163 | 3 | 4 | missing_lowering | high |
| `activated_effect:copy` | 42 | 40 | 3 | 4 | missing_lowering | high |
| `effect_clause:unparsed-when-this-aura` | 8 | 8 | 3 | 4 | missing_lowering | high |
| `activated_effect:draw` | 140 | 139 | 3 | 3 | missing_lowering | high |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:exile` | 849 | 2,083 | 2,083 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-at-the-beginning` | 845 | 2,018 | 2,018 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, effect_clause:unparsed-when-this-creature` | 844 | 2,045 | 2,045 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:return` | 839 | 2,022 | 2,022 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-whenever-this-creature` | 837 | 2,000 | 2,000 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:deal-damage` | 834 | 2,027 | 2,027 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:put-onto-battlefield` | 834 | 2,001 | 2,001 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:put-counter` | 832 | 2,009 | 2,009 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-whenever-you-cast` | 832 | 1,994 | 1,994 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, activated_effect:unparsed-you-get-an` | 831 | 2,015 | 2,016 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:life-change` | 831 | 2,005 | 2,005 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, replacement:damage-prevention` | 831 | 1,999 | 1,999 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, activated_effect:unparsed-the-next-time` | 830 | 1,992 | 1,992 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-whenever-one-or` | 830 | 1,992 | 1,992 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, activated_effect:exile` | 829 | 2,001 | 2,001 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, activated_effect:deal-damage` | 829 | 1,991 | 1,993 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:variable-nonmana-repeated-granted-instances-and-permanent-spells` | 829 | 1,991 | 1,991 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:typed-spell-additional-cost-clause` | 829 | 1,990 | 2,047 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-when-this-aura` | 829 | 1,990 | 1,990 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:exile, effect_clause:unparsed-at-the-beginning` | 828 | 2,063 | 2,063 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
