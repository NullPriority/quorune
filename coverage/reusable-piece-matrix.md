---
title: "Reusable rules piece matrix"
status: "generated"
authoritative_source: "coverage/reusable-piece-matrix.json.gz"
verified: "00b70cf1c9390cd483d270db259c3060c38d89673ec487926e32575c5375a00b"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Reusable rules piece matrix

Current Oracle IR material ability and residual spans plus all registered capabilities, mechanics, handlers, components, and pinned rule references. This inventories current source relations without claiming universal runtime completion.

Counts official ruling presence by Oracle ID. Ruling prose is not yet behaviorally classified, so these counts are composition evidence rather than coverage claims.

## Snapshot

- Profile: `commander_review`
- Ontology: `reusable-pieces-v1`
- Pieces: 5,607
- Cards indexed: 31,623
- Material abilities classified: 59,745
- Unclassified material spans: 0
- Mapped pinned rules: 1,859 / 3,309
- Applicable piece pairs: 214,091
- Covered piece pairs: 1,951

## Ontology classes

| Class | Pieces |
|---|---:|
| `actions_permissions` — Actions, permissions, and prohibitions | 548 |
| `card_forms` — Card types and specialized forms | 15 |
| `choices_continuations` — Modes, targets, choices, and continuations | 32 |
| `combat` — Combat | 55 |
| `compiler_cardprogram` — Compiler and CardProgram pieces | 3,021 |
| `continuous_effects` — Static abilities and continuous effects | 222 |
| `costs_mana` — Costs and mana | 25 |
| `events_mutations` — Typed events and mutations | 239 |
| `keyword_mechanics` — Keyword actions and keyword abilities | 874 |
| `multiplayer_commander` — Multiplayer, Commander, and profile pieces | 8 |
| `object_identity` — Object identity and lifetime | 134 |
| `one_shot_effects` — One-shot semantic effects | 302 |
| `players_format` — Players, relationships, and format state | 6 |
| `proposals` — Casting and activation proposals | 74 |
| `quantities` — Quantity and value expressions | 2 |
| `references` — References | 1 |
| `replacement_prevention` — Replacement and prevention | 46 |
| `triggers` — Triggers | 3 |

## Universal systems

| System | Status | Pieces | Blocking pieces |
|---|---|---:|---:|
| `action_legality_casting_activation_costs_mana` | `inventoried` | 647 | 7 |
| `combat` | `compositional` | 55 | 0 |
| `derived_characteristics_static_layers` | `inventoried` | 222 | 7 |
| `generic_triggers_stack_placement` | `inventoried` | 3 | 3 |
| `multiplayer_player_leaving_commander` | `compositional` | 14 | 0 |
| `objects_identity_zones_faces_copies` | `inventoried` | 149 | 3 |
| `replacement_prevention` | `inventoried` | 46 | 4 |
| `state_turn_loops_stabilization` | `inventoried` | 0 | 0 |
| `targets_modes_searches_references_choices` | `inventoried` | 35 | 11 |
| `typed_transactions_events_mutations` | `inventoried` | 541 | 91 |

## Highest current blocker leverage

| Piece | Class | Residuals | Sole blockers | Expected cards | Runtime | Assurance |
|---|---|---:|---:|---:|---|---|
| `residual.continuous_layer.continuous-effect-layers-and-dependencies` | `continuous_effects` | 1,934 | 784 | 784 | `absent` | `untested` |
| `residual.effect_clause.unparsed-clause-grammar` | `one_shot_effects` | 2,028 | 81 | 81 | `absent` | `untested` |
| `residual.activated_effect.unparsed-clause-grammar` | `one_shot_effects` | 761 | 61 | 61 | `absent` | `untested` |
| `residual.effect_clause.create-token` | `one_shot_effects` | 662 | 17 | 17 | `absent` | `untested` |
| `residual.effect_clause.return` | `one_shot_effects` | 713 | 11 | 11 | `absent` | `untested` |
| `residual.effect_clause.exile` | `one_shot_effects` | 881 | 10 | 10 | `absent` | `untested` |
| `residual.effect_clause.put-onto-battlefield` | `one_shot_effects` | 366 | 9 | 9 | `absent` | `untested` |
| `residual.effect_clause.deal-damage` | `one_shot_effects` | 691 | 7 | 7 | `absent` | `untested` |
| `residual.replacement.damage-prevention` | `replacement_prevention` | 99 | 6 | 6 | `absent` | `untested` |
| `residual.effect_clause.put-counter` | `one_shot_effects` | 605 | 5 | 5 | `absent` | `untested` |
| `residual.effect_clause.life-change` | `one_shot_effects` | 513 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.exile` | `one_shot_effects` | 211 | 4 | 4 | `absent` | `untested` |
| `residual.effect_clause.typed-spell-additional-cost-clause` | `one_shot_effects` | 61 | 4 | 4 | `absent` | `untested` |
| `residual.effect_clause.look-reveal` | `one_shot_effects` | 472 | 3 | 3 | `absent` | `untested` |
| `residual.activated_effect.deal-damage` | `one_shot_effects` | 175 | 3 | 3 | `absent` | `untested` |
| `residual.activated_effect.return` | `one_shot_effects` | 163 | 3 | 3 | `absent` | `untested` |
| `residual.activated_effect.create-token` | `one_shot_effects` | 152 | 3 | 3 | `absent` | `untested` |
| `residual.activated_effect.draw` | `one_shot_effects` | 140 | 3 | 3 | `absent` | `untested` |
| `residual.effect_clause.copy` | `one_shot_effects` | 68 | 3 | 3 | `absent` | `untested` |
| `residual.activated_effect.copy` | `one_shot_effects` | 42 | 3 | 3 | `absent` | `untested` |
| `residual.activated_effect.gain-control` | `one_shot_effects` | 23 | 3 | 3 | `absent` | `untested` |
| `residual.effect_clause.variable-nonmana-repeated-granted-instances-and-permanent-spells` | `one_shot_effects` | 5 | 3 | 3 | `absent` | `untested` |
| `residual.effect_clause.sacrifice` | `one_shot_effects` | 323 | 2 | 2 | `absent` | `untested` |
| `residual.activated_effect.put-counter` | `one_shot_effects` | 190 | 2 | 2 | `absent` | `untested` |
| `residual.activated_effect.put-onto-battlefield` | `one_shot_effects` | 128 | 2 | 2 | `absent` | `untested` |
| `residual.effect_clause.destroy` | `one_shot_effects` | 105 | 2 | 2 | `absent` | `untested` |
| `residual.activated_effect.tap-state` | `one_shot_effects` | 90 | 2 | 2 | `absent` | `untested` |
| `residual.effect_clause.remove-counter` | `one_shot_effects` | 75 | 2 | 2 | `absent` | `untested` |
| `residual.keyword_dependency.epic` | `keyword_mechanics` | 5 | 2 | 2 | `absent` | `untested` |
| `residual.keyword_dependency.time-travel` | `keyword_mechanics` | 4 | 2 | 2 | `absent` | `untested` |

## Boundary

Inventory and classification are not implementation or trust. Universal systems remain conservatively below snapshot-complete until all required rules, pieces, rulings, and interactions close.
