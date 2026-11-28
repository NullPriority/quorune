---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "6b2c7f2678231baf1449552c9be1c187bf25dadb5b537fd99bc2a2d5c6540af1"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":21286,"partial":5712,"unresolved":4625}`
- CardProgram states: `{"residual":10337,"trusted":21275,"untrusted":11}`
- Hard construction failures: 0
- Frontier fingerprint: `6b2c7f2678231baf1449552c9be1c187bf25dadb5b537fd99bc2a2d5c6540af1`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 1,693 | 1,342 | 709 | 1,693 | missing_lowering | very_high |
| `effect_clause:create-token` | 583 | 574 | 17 | 53 | missing_lowering | high |
| `effect_clause:unparsed-when-this-creature` | 181 | 180 | 15 | 50 | missing_lowering | high |
| `effect_clause:return` | 625 | 612 | 11 | 36 | missing_lowering | high |
| `effect_clause:exile` | 802 | 792 | 9 | 83 | missing_lowering | high |
| `effect_clause:unparsed-whenever-this-creature` | 129 | 128 | 9 | 14 | missing_lowering | high |
| `effect_clause:put-onto-battlefield` | 319 | 318 | 9 | 13 | missing_lowering | high |
| `activated_effect:unparsed-you-get-an` | 29 | 29 | 8 | 28 | missing_lowering | high |
| `keyword_dependency:harmonize` | 9 | 9 | 7 | 9 | missing_contract | medium |
| `replacement:damage-prevention` | 93 | 92 | 6 | 13 | missing_lowering | very_high |
| `effect_clause:unparsed-at-the-beginning` | 140 | 140 | 5 | 29 | missing_lowering | high |
| `effect_clause:deal-damage` | 638 | 628 | 5 | 24 | missing_lowering | high |
| `activated_effect:unparsed-the-next-time` | 14 | 14 | 5 | 6 | missing_lowering | high |
| `effect_clause:put-counter` | 553 | 550 | 3 | 18 | missing_lowering | high |
| `activated_effect:create-token` | 143 | 138 | 3 | 17 | missing_lowering | high |
| `effect_clause:life-change` | 440 | 435 | 3 | 16 | missing_lowering | high |
| `activated_effect:exile` | 187 | 177 | 3 | 12 | missing_lowering | high |
| `effect_clause:variable-nonmana-repeated-granted-instances-and-permanent-spells` | 5 | 5 | 3 | 5 | missing_lowering | high |
| `activated_effect:return` | 142 | 142 | 3 | 4 | missing_lowering | high |
| `activated_effect:copy` | 37 | 35 | 3 | 4 | missing_lowering | high |
| `effect_clause:copy` | 62 | 62 | 3 | 3 | missing_lowering | high |
| `effect_clause:typed-spell-additional-cost-clause` | 55 | 55 | 3 | 3 | missing_lowering | high |
| `activated_effect:gain-control` | 23 | 23 | 3 | 3 | missing_lowering | high |
| `activated_effect:unparsed-the-next-1` | 6 | 6 | 3 | 3 | missing_lowering | high |
| `effect_clause:remove-counter` | 74 | 74 | 2 | 11 | missing_lowering | high |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:exile` | 770 | 1,826 | 1,826 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, effect_clause:unparsed-when-this-creature` | 767 | 1,796 | 1,796 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-at-the-beginning` | 766 | 1,772 | 1,772 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:return` | 762 | 1,779 | 1,779 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-whenever-this-creature` | 760 | 1,757 | 1,757 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:put-onto-battlefield` | 757 | 1,756 | 1,756 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, activated_effect:unparsed-you-get-an` | 756 | 1,771 | 1,772 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, keyword_dependency:harmonize` | 755 | 1,752 | 1,752 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:deal-damage` | 754 | 1,767 | 1,767 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, replacement:damage-prevention` | 754 | 1,756 | 1,756 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:put-counter` | 753 | 1,761 | 1,761 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:life-change` | 753 | 1,759 | 1,759 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, activated_effect:unparsed-the-next-time` | 753 | 1,749 | 1,749 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-whenever-one-or` | 752 | 1,748 | 1,748 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-whenever-you-cast` | 752 | 1,748 | 1,748 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:variable-nonmana-repeated-granted-instances-and-permanent-spells` | 752 | 1,748 | 1,748 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, activated_effect:create-token` | 751 | 1,760 | 1,762 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, activated_effect:exile` | 751 | 1,755 | 1,755 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:remove-counter` | 751 | 1,754 | 1,754 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:linked-entry-card-name-ownership` | 751 | 1,751 | 1,751 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
