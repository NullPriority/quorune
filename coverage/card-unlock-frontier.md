---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "4ef239132b5ab7c4f323f7f0b78aa01a9be53b4b289a6952c91368b4aee37a76"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":19076,"partial":6831,"unresolved":5716}`
- CardProgram states: `{"residual":12547,"trusted":19075,"untrusted":1}`
- Hard construction failures: 0
- Frontier fingerprint: `4ef239132b5ab7c4f323f7f0b78aa01a9be53b4b289a6952c91368b4aee37a76`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 2,125 | 1,716 | 890 | 2,125 | missing_lowering | very_high |
| `effect_clause:create-token` | 712 | 702 | 26 | 73 | missing_lowering | high |
| `effect_clause:unparsed-when-this-creature` | 210 | 209 | 23 | 60 | missing_lowering | high |
| `effect_clause:unparsed-whenever-this-creature` | 152 | 151 | 21 | 28 | missing_lowering | high |
| `effect_clause:deal-damage` | 745 | 725 | 20 | 58 | missing_lowering | high |
| `effect_clause:return` | 749 | 736 | 18 | 43 | missing_lowering | high |
| `effect_clause:sacrifice` | 364 | 363 | 18 | 27 | missing_lowering | high |
| `effect_clause:exile` | 924 | 911 | 10 | 97 | missing_lowering | high |
| `activated_effect:unparsed-you-get-an` | 37 | 37 | 10 | 36 | missing_lowering | high |
| `effect_clause:life-change` | 591 | 584 | 10 | 31 | missing_lowering | high |
| `effect_clause:put-onto-battlefield` | 378 | 377 | 10 | 17 | missing_lowering | high |
| `effect_clause:typed-spell-additional-cost-clause` | 70 | 70 | 9 | 9 | missing_lowering | high |
| `effect_clause:destroy-mass` | 128 | 122 | 8 | 18 | missing_lowering | high |
| `replacement:damage-prevention` | 115 | 114 | 8 | 18 | missing_lowering | very_high |
| `effect_clause:unparsed-at-the-beginning` | 168 | 167 | 7 | 33 | missing_lowering | high |
| `effect_clause:look-reveal` | 543 | 542 | 7 | 8 | missing_lowering | high |
| `effect_clause:put-counter` | 623 | 619 | 6 | 25 | missing_lowering | high |
| `effect_clause:remove-counter` | 83 | 83 | 5 | 15 | missing_lowering | high |
| `activated_effect:life-change` | 111 | 107 | 5 | 6 | missing_lowering | high |
| `activated_effect:unparsed-the-next-time` | 15 | 15 | 5 | 6 | missing_lowering | high |
| `effect_clause:tap-state` | 364 | 358 | 4 | 19 | missing_lowering | high |
| `activated_effect:exile` | 221 | 208 | 4 | 15 | missing_lowering | high |
| `activated_effect:put-onto-battlefield` | 143 | 141 | 4 | 9 | missing_lowering | high |
| `activated_effect:return` | 171 | 171 | 4 | 5 | missing_lowering | high |
| `activated_effect:unparsed-target-player-shuffles` | 5 | 5 | 4 | 5 | missing_lowering | high |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, effect_clause:unparsed-when-this-creature` | 969 | 2,258 | 2,258 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-whenever-this-creature` | 964 | 2,213 | 2,213 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:exile` | 963 | 2,282 | 2,282 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:deal-damage` | 961 | 2,243 | 2,243 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:return` | 960 | 2,228 | 2,228 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-at-the-beginning` | 960 | 2,218 | 2,218 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:sacrifice` | 957 | 2,212 | 2,212 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, activated_effect:unparsed-you-get-an` | 951 | 2,221 | 2,222 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:life-change` | 951 | 2,216 | 2,216 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:put-onto-battlefield` | 949 | 2,202 | 2,202 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:typed-spell-additional-cost-clause` | 948 | 2,194 | 2,255 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:put-counter` | 947 | 2,210 | 2,210 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:tap-state` | 947 | 2,204 | 2,204 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:destroy-mass` | 947 | 2,203 | 2,203 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, replacement:damage-prevention` | 947 | 2,203 | 2,203 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:look-reveal` | 946 | 2,193 | 2,193 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:unparsed-whenever-you-cast` | 946 | 2,193 | 2,193 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, effect_clause:unparsed-whenever-this-creature` | 945 | 2,226 | 2,226 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:unparsed-when-this-creature, effect_clause:remove-counter` | 945 | 2,200 | 2,200 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:create-token, effect_clause:exile` | 944 | 2,295 | 2,295 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
