---
title: "Commander card-unlock frontier"
status: "generated"
authoritative_source: "coverage/card-unlock-frontier.json.gz"
verified: "55b3ffb2c593e1ea9866955db76b03d6b03a8fc0bd873779ffcab93bc321b30b"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Commander card-unlock frontier

This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.

## Snapshot

- Cards considered: 31,623
- Oracle states: `{"exact":8956,"partial":11313,"unresolved":11354}`
- CardProgram states: `{"residual":22667,"trusted":8956}`
- Hard construction failures: 0
- Frontier fingerprint: `55b3ffb2c593e1ea9866955db76b03d6b03a8fc0bd873779ffcab93bc321b30b`

## Highest-leverage single families

| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |
|---|---:|---:|---:|---:|---|---|
| `continuous_layer:continuous-effect-layers-and-dependencies` | 5,330 | 4,478 | 2,293 | 5,330 | missing_lowering | very_high |
| `keyword_dependency:banding` | 24 | 24 | 19 | 24 | missing_contract | medium |
| `effect_clause:typed-spell-additional-cost-clause` | 106 | 106 | 18 | 18 | missing_lowering | high |
| `effect_clause:life-change` | 496 | 494 | 16 | 43 | missing_lowering | high |
| `effect_clause:exile` | 552 | 540 | 14 | 73 | missing_lowering | high |
| `keyword_dependency:start-your-engines` | 40 | 40 | 14 | 40 | missing_contract | medium |
| `activated_effect:put-onto-battlefield` | 221 | 219 | 14 | 32 | missing_lowering | high |
| `effect_clause:create-token` | 546 | 531 | 13 | 73 | missing_lowering | high |
| `replacement:damage-prevention` | 142 | 140 | 13 | 29 | missing_lowering | very_high |
| `keyword_dependency:equip` | 25 | 25 | 12 | 25 | missing_contract | medium |
| `keyword_dependency:living-weapon` | 19 | 19 | 12 | 19 | missing_contract | medium |
| `activated_effect:create-token` | 290 | 283 | 11 | 47 | missing_lowering | high |
| `effect_clause:sacrifice` | 110 | 110 | 11 | 34 | missing_lowering | high |
| `keyword_dependency:rebound` | 34 | 34 | 11 | 34 | missing_contract | medium |
| `effect_clause:return` | 568 | 548 | 11 | 23 | missing_lowering | high |
| `keyword_dependency:myriad` | 23 | 23 | 11 | 23 | missing_contract | medium |
| `effect_clause:unparsed-splice-onto-arcane` | 22 | 22 | 11 | 22 | missing_lowering | high |
| `activated_effect:unparsed-this-creature-can` | 20 | 20 | 11 | 12 | missing_lowering | high |
| `keyword_dependency:umbra-armor` | 15 | 15 | 10 | 15 | missing_contract | medium |
| `activated_effect:life-change` | 202 | 191 | 9 | 17 | missing_lowering | high |
| `keyword_dependency:split-second` | 21 | 21 | 8 | 21 | missing_contract | medium |
| `keyword_dependency:extort` | 18 | 17 | 8 | 18 | missing_contract | medium |
| `keyword_dependency:for-mirrodin` | 13 | 13 | 8 | 13 | missing_contract | medium |
| `keyword_dependency:enlist` | 12 | 12 | 8 | 12 | missing_contract | medium |
| `effect_clause:add-mana` | 57 | 57 | 8 | 11 | missing_lowering | high |

## Highest-leverage bounded bundles

| Families | Exact cards | Exact abilities | Residuals |
|---|---:|---:|---:|
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:start-your-engines` | 2,335 | 5,394 | 5,394 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:equip` | 2,333 | 5,395 | 5,395 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:start-your-engines` | 2,333 | 5,388 | 5,476 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:create-token` | 2,331 | 5,417 | 5,427 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:life-change, keyword_dependency:start-your-engines` | 2,331 | 5,413 | 5,413 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, activated_effect:put-onto-battlefield` | 2,331 | 5,402 | 5,403 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:myriad` | 2,331 | 5,393 | 5,393 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:equip` | 2,331 | 5,379 | 5,379 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:typed-spell-additional-cost-clause` | 2,331 | 5,372 | 5,460 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:exile, keyword_dependency:start-your-engines` | 2,329 | 5,443 | 5,443 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:create-token` | 2,329 | 5,401 | 5,411 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:life-change` | 2,329 | 5,397 | 5,397 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:living-weapon` | 2,329 | 5,389 | 5,389 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, activated_effect:put-onto-battlefield` | 2,329 | 5,386 | 5,387 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, keyword_dependency:myriad` | 2,329 | 5,377 | 5,377 |
| `continuous_layer:continuous-effect-layers-and-dependencies, effect_clause:typed-spell-additional-cost-clause, keyword_dependency:equip` | 2,329 | 5,373 | 5,461 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, effect_clause:create-token` | 2,328 | 5,443 | 5,443 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, replacement:damage-prevention` | 2,328 | 5,399 | 5,401 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:start-your-engines, keyword_dependency:umbra-armor` | 2,328 | 5,385 | 5,385 |
| `continuous_layer:continuous-effect-layers-and-dependencies, keyword_dependency:banding, effect_clause:exile` | 2,327 | 5,427 | 5,427 |

## Hard construction failures

- None in the pinned Commander-legal snapshot.

## Boundary

This is a minimum-known-blocker frontier for the pinned Commander-legal snapshot. It does not prove complete Comprehensive Rules behavior.
The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.
