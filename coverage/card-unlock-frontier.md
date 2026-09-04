---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "2a01ae6c829c8925aa3e7ce5001aebcf5c369ffb5516d92690af786ee7065596"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":8790,"partial":11335,"unresolved":11498}`
- CardProgram states: `{"residual":22833,"trusted":8790}`
- Hard construction failures: 0
- Frontier fingerprint: `2a01ae6c829c8925aa3e7ce5001aebcf5c369ffb5516d92690af786ee7065596`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 5,484 | 4,613 | 2,360 | 5,484 | missing_lowering | very_high |
| `activated_effect:create-token` | 314 | 307 | 26 | 68 | missing_lowering | high |
| `effect_clause:create-token` | 558 | 543 | 19 | 81 | missing_lowering | high |
| `keyword_dependency:banding` | 24 | 24 | 19 | 24 | missing_contract | medium |
| `effect_clause:typed-spell-additional-cost-clause` | 106 | 106 | 18 | 18 | missing_lowering | high |
| `effect_clause:life-change` | 497 | 495 | 16 | 43 | missing_lowering | high |
| `keyword_dependency:start-your-engines` | 40 | 40 | 14 | 40 | missing_contract | medium |
| `activated_effect:put-onto-battlefield` | 221 | 219 | 14 | 32 | missing_lowering | high |
| `effect_clause:exile` | 552 | 540 | 13 | 73 | missing_lowering | high |
| `replacement:damage-prevention` | 142 | 140 | 13 | 29 | missing_lowering | very_high |
| `keyword_dependency:equip` | 25 | 25 | 12 | 25 | missing_contract | medium |
| `activated_effect:unparsed-investigate` | 13 | 13 | 12 | 13 | missing_lowering | high |
| `effect_clause:sacrifice` | 110 | 110 | 11 | 34 | missing_lowering | high |
| `keyword_dependency:rebound` | 34 | 34 | 11 | 34 | missing_contract | medium |
| `effect_clause:return` | 568 | 548 | 11 | 23 | missing_lowering | high |
| `keyword_dependency:myriad` | 23 | 23 | 11 | 23 | missing_contract | medium |
| `effect_clause:unparsed-splice-onto-arcane` | 22 | 22 | 11 | 22 | missing_lowering | high |
| `keyword_dependency:living-weapon` | 19 | 19 | 11 | 19 | missing_contract | medium |
| `activated_effect:unparsed-this-creature-can` | 20 | 20 | 11 | 12 | missing_lowering | high |
| `keyword_dependency:umbra-armor` | 15 | 15 | 10 | 15 | missing_contract | medium |
| `activated_effect:life-change` | 203 | 192 | 9 | 17 | missing_lowering | high |
| `keyword_dependency:split-second` | 21 | 21 | 8 | 21 | missing_contract | medium |
| `keyword_dependency:extort` | 18 | 17 | 8 | 18 | missing_contract | medium |
| `keyword_dependency:for-mirrodin` | 13 | 13 | 8 | 13 | missing_contract | medium |
| `keyword_dependency:enlist` | 12 | 12 | 8 | 12 | missing_contract | medium |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:start-your-engines` | 2,414 | 5,592 | 5,604 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:banding` | 2,412 | 5,576 | 5,588 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:create-token` | 2,411 | 5,633 | 5,645 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:equip` | 2,410 | 5,577 | 5,589 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:typed-spell-additional-cost-clause` | 2,410 | 5,570 | 5,670 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:life-change` | 2,408 | 5,595 | 5,607 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, activated_effect:put-onto-battlefield` | 2,408 | 5,584 | 5,597 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:myriad` | 2,408 | 5,575 | 5,587 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:living-weapon` | 2,406 | 5,571 | 5,583 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:exile` | 2,405 | 5,625 | 5,637 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, replacement:damage-prevention` | 2,405 | 5,581 | 5,595 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:umbra-armor` | 2,405 | 5,567 | 5,579 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, activated_effect:unparsed-investigate` | 2,404 | 5,565 | 5,577 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:sacrifice` | 2,403 | 5,586 | 5,598 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:rebound` | 2,403 | 5,586 | 5,598 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:return` | 2,403 | 5,575 | 5,587 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, effect_clause:unparsed-splice-onto-arcane` | 2,403 | 5,574 | 5,586 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, activated_effect:unparsed-this-creature-can` | 2,403 | 5,564 | 5,577 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:split-second` | 2,402 | 5,573 | 5,585 |
| `continuous_layer:continuous-effect-layers-and-dependencies, activated_effect:create-token, keyword_dependency:extort` | 2,402 | 5,570 | 5,582 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
