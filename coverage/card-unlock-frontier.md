---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "d9337c0af868ed197581e59d7849752dd545c79e31ffc31bbf379456a4b9067f"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":15537,"partial":8681,"unresolved":7405}`
- CardProgram states: `{"residual":16088,"trusted":15535}`
- Hard construction failures: 0
- Frontier fingerprint: `d9337c0af868ed197581e59d7849752dd545c79e31ffc31bbf379456a4b9067f`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 2,712 | 2,218 | 1,140 | 2,712 | missing_lowering | very_high |
| `keyword_dependency:banding` | 24 | 24 | 19 | 24 | missing_contract | medium |
| `replacement:damage-prevention` | 123 | 121 | 14 | 25 | missing_lowering | very_high |
| `keyword_dependency:cipher` | 15 | 15 | 11 | 15 | missing_contract | medium |
| `activated_effect:create-token` | 231 | 226 | 10 | 32 | missing_lowering | high |
| `effect_clause:typed-spell-additional-cost-clause` | 72 | 72 | 10 | 10 | missing_lowering | high |
| `keyword_dependency:assist` | 16 | 16 | 9 | 16 | missing_contract | medium |
| `activated_effect:unparsed-you-get-an` | 69 | 69 | 8 | 67 | missing_lowering | high |
| `activated_effect:exile` | 260 | 246 | 8 | 20 | missing_lowering | high |
| `effect_clause:life-change` | 353 | 351 | 6 | 18 | missing_lowering | high |
| `effect_clause:destroy-mass` | 137 | 128 | 6 | 17 | missing_lowering | high |
| `effect_clause:return` | 414 | 401 | 6 | 16 | missing_lowering | high |
| `effect_clause:counter` | 152 | 152 | 6 | 12 | missing_lowering | high |
| `keyword_dependency:phasing` | 12 | 12 | 6 | 12 | missing_contract | medium |
| `activated_effect:life-change` | 150 | 144 | 6 | 11 | missing_lowering | high |
| `effect_clause:unparsed-target-spell-or` | 7 | 7 | 6 | 6 | missing_lowering | high |
| `effect_clause:exile` | 464 | 457 | 5 | 50 | missing_lowering | high |
| `keyword_dependency:equip` | 8 | 8 | 5 | 8 | missing_contract | medium |
| `activated_effect:draw` | 218 | 217 | 5 | 6 | missing_lowering | high |
| `activated_effect:unparsed-the-next-time` | 15 | 15 | 5 | 6 | missing_lowering | high |
| `activated_effect:unparsed-target-creature-gets` | 14 | 14 | 5 | 6 | missing_lowering | high |
| `activated_effect:destroy-target` | 53 | 53 | 5 | 5 | missing_lowering | high |
| `effect_clause:create-token` | 420 | 409 | 4 | 47 | missing_lowering | high |
| `effect_clause:destroy-target` | 268 | 260 | 4 | 44 | missing_lowering | high |
| `activated_effect:put-onto-battlefield` | 162 | 160 | 4 | 11 | missing_lowering | high |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, replacement:damage-prevention` | 1,174 | 2,761 | 2,761 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:create-token` | 1,171 | 2,768 | 2,775 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:cipher` | 1,171 | 2,751 | 2,751 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:typed-spell-additional-cost-clause` | 1,170 | 2,746 | 2,808 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:unparsed-you-get-an` | 1,169 | 2,803 | 2,805 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:exile` | 1,169 | 2,756 | 2,758 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:assist` | 1,169 | 2,752 | 2,752 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:life-change` | 1,166 | 2,754 | 2,754 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:destroy-mass` | 1,166 | 2,753 | 2,753 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:return` | 1,166 | 2,752 | 2,752 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:counter` | 1,166 | 2,748 | 2,748 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:phasing` | 1,166 | 2,748 | 2,748 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:life-change` | 1,166 | 2,747 | 2,747 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:put-onto-battlefield` | 1,166 | 2,747 | 2,747 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:equip` | 1,166 | 2,744 | 2,744 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:unparsed-target-spell-or` | 1,166 | 2,742 | 2,742 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:exile` | 1,165 | 2,786 | 2,786 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, activated_effect:create-token` | 1,165 | 2,769 | 2,776 |
| `continuous_layer:continuous-effect-layers-and-dependencies, replacement:damage-prevention, keyword_dependency:cipher` | 1,165 | 2,752 | 2,752 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:draw` | 1,165 | 2,742 | 2,742 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
