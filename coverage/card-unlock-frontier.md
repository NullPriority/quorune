---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "96479fc351b63d0ef48726d9f70ac3c88cc6603602bc6d007e3bf8f8434514ae"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":19625,"partial":6536,"unresolved":5462}`
- CardProgram states: `{"residual":11998,"trusted":19622,"untrusted":3}`
- Hard construction failures: 0
- Frontier fingerprint: `96479fc351b63d0ef48726d9f70ac3c88cc6603602bc6d007e3bf8f8434514ae`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 2,070 | 1,664 | 850 | 2,070 | missing_lowering | very_high |
| `effect_clause:unparsed-when-this-creature` | 204 | 203 | 21 | 58 | missing_lowering | high |
| `effect_clause:create-token` | 681 | 671 | 17 | 61 | missing_lowering | high |
| `effect_clause:return` | 740 | 727 | 15 | 40 | missing_lowering | high |
| `effect_clause:exile` | 898 | 885 | 10 | 97 | missing_lowering | high |
| `effect_clause:deal-damage` | 718 | 699 | 10 | 45 | missing_lowering | high |
| `effect_clause:put-onto-battlefield` | 371 | 370 | 9 | 15 | missing_lowering | high |
| `effect_clause:unparsed-whenever-this-creature` | 137 | 136 | 9 | 14 | missing_lowering | high |
| `effect_clause:typed-spell-additional-cost-clause` | 69 | 69 | 8 | 8 | missing_lowering | high |
| `effect_clause:put-counter` | 615 | 611 | 7 | 25 | missing_lowering | high |
| `replacement:damage-prevention` | 105 | 104 | 7 | 14 | missing_lowering | very_high |
| `effect_clause:unparsed-at-the-beginning` | 161 | 161 | 6 | 32 | missing_lowering | high |
| `activated_effect:unparsed-you-get-an` | 32 | 32 | 5 | 31 | missing_lowering | high |
| `effect_clause:life-change` | 543 | 536 | 5 | 21 | missing_lowering | high |
| `activated_effect:unparsed-the-next-time` | 15 | 15 | 5 | 6 | missing_lowering | high |
| `mechanic_dependency:colored-hand-reveal-results` | 5 | 5 | 5 | 5 | missing_contract | high |
| `activated_effect:exile` | 213 | 200 | 4 | 15 | missing_lowering | high |
| `effect_clause:destroy-mass` | 122 | 116 | 4 | 14 | missing_lowering | high |
| `effect_clause:sacrifice` | 335 | 334 | 4 | 12 | missing_lowering | high |
| `effect_clause:look-reveal` | 482 | 481 | 4 | 5 | missing_lowering | high |
| `effect_clause:copy` | 78 | 78 | 4 | 4 | missing_lowering | high |
| `activated_effect:destroy-target` | 37 | 37 | 4 | 4 | missing_lowering | high |
| `activated_effect:create-token` | 157 | 152 | 3 | 17 | missing_lowering | high |
| `effect_clause:unparsed-whenever-you-cast` | 27 | 27 | 3 | 8 | missing_lowering | high |
| `effect_clause:destroy` | 107 | 107 | 3 | 7 | missing_lowering | high |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:exile` | 921 | 2,225 | 2,225 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:create-token` | 917 | 2,189 | 2,189 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-at-the-beginning` | 917 | 2,160 | 2,160 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:return` | 915 | 2,168 | 2,168 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:deal-damage` | 909 | 2,173 | 2,173 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-whenever-this-creature` | 909 | 2,142 | 2,142 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:put-counter` | 906 | 2,153 | 2,153 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:put-onto-battlefield` | 906 | 2,143 | 2,143 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:typed-spell-additional-cost-clause` | 905 | 2,136 | 2,197 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, activated_effect:unparsed-you-get-an` | 904 | 2,159 | 2,160 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:life-change` | 904 | 2,149 | 2,149 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, replacement:damage-prevention` | 904 | 2,142 | 2,142 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-whenever-you-cast` | 904 | 2,136 | 2,136 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, activated_effect:unparsed-the-next-time` | 902 | 2,134 | 2,134 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, mechanic_dependency:colored-hand-reveal-results` | 902 | 2,133 | 2,133 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, activated_effect:exile` | 901 | 2,143 | 2,143 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:destroy-mass` | 901 | 2,142 | 2,142 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:sacrifice` | 901 | 2,140 | 2,140 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, activated_effect:deal-damage` | 901 | 2,134 | 2,136 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:look-reveal` | 901 | 2,133 | 2,133 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
