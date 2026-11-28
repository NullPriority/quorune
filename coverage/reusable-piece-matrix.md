---
title: "Reusable rules piece matrix"
status: "generated"
authoritative_source: "coverage/reusable-piece-matrix.json.gz"
verified: "a902d70e2bd5de8fcc8fe8dcb259c56c72d395e96fd78ff4709bd8d4c21721f6"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Reusable rules piece matrix

Current Oracle IR material ability and residual spans plus all registered capabilities, mechanics, handlers, components, and pinned rule references. This inventories current source relations without claiming universal runtime completion.

Counts official ruling presence by Oracle ID. Ruling prose is not yet behaviorally classified, so these counts are composition evidence rather than coverage claims.

## Snapshot

- Profile: `commander_review`
- Ontology: `reusable-pieces-v1`
- Pieces: 7,291
- Cards indexed: 31,623
- Material abilities classified: 59,519
- Unclassified material spans: 0
- Mapped pinned rules: 2,044 / 3,309
- Applicable piece pairs: 245,374
- Covered piece pairs: 1,956

## Ontology classes

| Class | Pieces |
|---|---:|
| `actions_permissions` — Actions, permissions, and prohibitions | 939 |
| `card_forms` — Card types and specialized forms | 17 |
| `choices_continuations` — Modes, targets, choices, and continuations | 44 |
| `combat` — Combat | 68 |
| `compiler_cardprogram` — Compiler and CardProgram pieces | 3,678 |
| `continuous_effects` — Static abilities and continuous effects | 285 |
| `costs_mana` — Costs and mana | 63 |
| `events_mutations` — Typed events and mutations | 358 |
| `keyword_mechanics` — Keyword actions and keyword abilities | 1,062 |
| `multiplayer_commander` — Multiplayer, Commander, and profile pieces | 8 |
| `object_identity` — Object identity and lifetime | 248 |
| `one_shot_effects` — One-shot semantic effects | 343 |
| `players_format` — Players, relationships, and format state | 7 |
| `proposals` — Casting and activation proposals | 110 |
| `quantities` — Quantity and value expressions | 2 |
| `references` — References | 1 |
| `replacement_prevention` — Replacement and prevention | 55 |
| `triggers` — Triggers | 3 |

## Universal systems

| System | Status | Pieces | Blocking pieces |
|---|---|---:|---:|
| `action_legality_casting_activation_costs_mana` | `inventoried` | 1,112 | 8 |
| `combat` | `compositional` | 68 | 0 |
| `derived_characteristics_static_layers` | `inventoried` | 285 | 7 |
| `generic_triggers_stack_placement` | `inventoried` | 3 | 3 |
| `multiplayer_player_leaving_commander` | `compositional` | 15 | 0 |
| `objects_identity_zones_faces_copies` | `inventoried` | 265 | 3 |
| `replacement_prevention` | `inventoried` | 55 | 4 |
| `state_turn_loops_stabilization` | `inventoried` | 0 | 0 |
| `targets_modes_searches_references_choices` | `inventoried` | 47 | 11 |
| `typed_transactions_events_mutations` | `inventoried` | 701 | 94 |

## Highest current blocker leverage

| Piece | Class | Residuals | Sole blockers | Expected cards | Runtime | Assurance |
|---|---|---:|---:|---:|---|---|
| `residual.continuous_layer.continuous-effect-layers-and-dependencies` | `continuous_effects` | 1,693 | 709 | 709 | `absent` | `untested` |
| `residual.effect_clause.unparsed-clause-grammar` | `one_shot_effects` | 1,808 | 72 | 72 | `absent` | `untested` |
| `residual.activated_effect.unparsed-clause-grammar` | `one_shot_effects` | 685 | 57 | 57 | `absent` | `untested` |
| `residual.effect_clause.create-token` | `one_shot_effects` | 583 | 17 | 17 | `absent` | `untested` |
| `residual.effect_clause.return` | `one_shot_effects` | 625 | 11 | 11 | `absent` | `untested` |
| `residual.effect_clause.exile` | `one_shot_effects` | 802 | 9 | 9 | `absent` | `untested` |
| `residual.effect_clause.put-onto-battlefield` | `one_shot_effects` | 319 | 9 | 9 | `absent` | `untested` |
| `residual.keyword_dependency.harmonize` | `keyword_mechanics` | 9 | 7 | 7 | `absent` | `untested` |
| `residual.replacement.damage-prevention` | `replacement_prevention` | 93 | 6 | 6 | `absent` | `untested` |
| `residual.effect_clause.deal-damage` | `one_shot_effects` | 638 | 5 | 5 | `absent` | `untested` |
| `residual.effect_clause.put-counter` | `one_shot_effects` | 553 | 3 | 3 | `absent` | `untested` |
| `residual.effect_clause.life-change` | `one_shot_effects` | 440 | 3 | 3 | `absent` | `untested` |
| `residual.activated_effect.exile` | `one_shot_effects` | 187 | 3 | 3 | `absent` | `untested` |
| `residual.activated_effect.create-token` | `one_shot_effects` | 143 | 3 | 3 | `absent` | `untested` |
| `residual.activated_effect.return` | `one_shot_effects` | 142 | 3 | 3 | `absent` | `untested` |
| `residual.effect_clause.copy` | `one_shot_effects` | 62 | 3 | 3 | `absent` | `untested` |
| `residual.effect_clause.typed-spell-additional-cost-clause` | `one_shot_effects` | 55 | 3 | 3 | `absent` | `untested` |
| `residual.activated_effect.copy` | `one_shot_effects` | 37 | 3 | 3 | `absent` | `untested` |
| `residual.activated_effect.gain-control` | `one_shot_effects` | 23 | 3 | 3 | `absent` | `untested` |
| `residual.effect_clause.variable-nonmana-repeated-granted-instances-and-permanent-spells` | `one_shot_effects` | 5 | 3 | 3 | `absent` | `untested` |
| `residual.effect_clause.look-reveal` | `one_shot_effects` | 421 | 2 | 2 | `absent` | `untested` |
| `residual.effect_clause.sacrifice` | `one_shot_effects` | 296 | 2 | 2 | `absent` | `untested` |
| `residual.activated_effect.put-counter` | `one_shot_effects` | 180 | 2 | 2 | `absent` | `untested` |
| `residual.activated_effect.deal-damage` | `one_shot_effects` | 165 | 2 | 2 | `absent` | `untested` |
| `residual.activated_effect.draw` | `one_shot_effects` | 121 | 2 | 2 | `absent` | `untested` |
| `residual.activated_effect.put-onto-battlefield` | `one_shot_effects` | 106 | 2 | 2 | `absent` | `untested` |
| `residual.effect_clause.destroy` | `one_shot_effects` | 98 | 2 | 2 | `absent` | `untested` |
| `residual.effect_clause.remove-counter` | `one_shot_effects` | 74 | 2 | 2 | `absent` | `untested` |
| `residual.keyword_dependency.epic` | `keyword_mechanics` | 5 | 2 | 2 | `absent` | `untested` |
| `residual.keyword_dependency.time-travel` | `keyword_mechanics` | 4 | 2 | 2 | `absent` | `untested` |

## Boundary

Inventory and classification are not implementation or trust. Universal systems remain conservatively below snapshot-complete until all required rules, pieces, rulings, and interactions close.
