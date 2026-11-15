---
title: "Reusable rules piece matrix"
status: "generated"
authoritative_source: "coverage/reusable-piece-matrix.json.gz"
verified: "b54bf8f4fe81e957c949e91025fafd2b572cf98aafc06a6866c29ea15c62fbf5"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Reusable rules piece matrix

Current Oracle IR material ability and residual spans plus all registered capabilities, mechanics, handlers, components, and pinned rule references. This inventories current source relations without claiming universal runtime completion.

Counts official ruling presence by Oracle ID. Ruling prose is not yet behaviorally classified, so these counts are composition evidence rather than coverage claims.

## Snapshot

- Profile: `commander_review`
- Ontology: `reusable-pieces-v1`
- Pieces: 4,805
- Cards indexed: 31,623
- Material abilities classified: 59,783
- Unclassified material spans: 0
- Mapped pinned rules: 1,768 / 3,309
- Applicable piece pairs: 195,047
- Covered piece pairs: 1,941

## Ontology classes

| Class | Pieces |
|---|---:|
| `actions_permissions` — Actions, permissions, and prohibitions | 422 |
| `card_forms` — Card types and specialized forms | 14 |
| `choices_continuations` — Modes, targets, choices, and continuations | 25 |
| `combat` — Combat | 52 |
| `compiler_cardprogram` — Compiler and CardProgram pieces | 2,630 |
| `continuous_effects` — Static abilities and continuous effects | 174 |
| `costs_mana` — Costs and mana | 22 |
| `events_mutations` — Typed events and mutations | 180 |
| `keyword_mechanics` — Keyword actions and keyword abilities | 790 |
| `multiplayer_commander` — Multiplayer, Commander, and profile pieces | 7 |
| `object_identity` — Object identity and lifetime | 94 |
| `one_shot_effects` — One-shot semantic effects | 281 |
| `players_format` — Players, relationships, and format state | 6 |
| `proposals` — Casting and activation proposals | 66 |
| `quantities` — Quantity and value expressions | 2 |
| `references` — References | 1 |
| `replacement_prevention` — Replacement and prevention | 36 |
| `triggers` — Triggers | 3 |

## Universal systems

| System | Status | Pieces | Blocking pieces |
|---|---|---:|---:|
| `action_legality_casting_activation_costs_mana` | `inventoried` | 510 | 6 |
| `combat` | `compositional` | 52 | 0 |
| `derived_characteristics_static_layers` | `inventoried` | 174 | 7 |
| `generic_triggers_stack_placement` | `inventoried` | 3 | 3 |
| `multiplayer_player_leaving_commander` | `compositional` | 13 | 0 |
| `objects_identity_zones_faces_copies` | `inventoried` | 108 | 3 |
| `replacement_prevention` | `inventoried` | 36 | 4 |
| `state_turn_loops_stabilization` | `inventoried` | 0 | 0 |
| `targets_modes_searches_references_choices` | `inventoried` | 28 | 12 |
| `typed_transactions_events_mutations` | `inventoried` | 461 | 90 |

## Highest current blocker leverage

| Piece | Class | Residuals | Sole blockers | Expected cards | Runtime | Assurance |
|---|---|---:|---:|---:|---|---|
| `residual.continuous_layer.continuous-effect-layers-and-dependencies` | `continuous_effects` | 2,125 | 890 | 890 | `absent` | `untested` |
| `residual.effect_clause.unparsed-clause-grammar` | `one_shot_effects` | 2,227 | 116 | 116 | `absent` | `untested` |
| `residual.activated_effect.unparsed-clause-grammar` | `one_shot_effects` | 829 | 79 | 79 | `absent` | `untested` |
| `residual.effect_clause.create-token` | `one_shot_effects` | 712 | 26 | 26 | `absent` | `untested` |
| `residual.effect_clause.deal-damage` | `one_shot_effects` | 745 | 20 | 20 | `absent` | `untested` |
| `residual.effect_clause.return` | `one_shot_effects` | 749 | 18 | 18 | `absent` | `untested` |
| `residual.effect_clause.sacrifice` | `one_shot_effects` | 364 | 18 | 18 | `absent` | `untested` |
| `residual.effect_clause.exile` | `one_shot_effects` | 924 | 10 | 10 | `absent` | `untested` |
| `residual.effect_clause.life-change` | `one_shot_effects` | 591 | 10 | 10 | `absent` | `untested` |
| `residual.effect_clause.put-onto-battlefield` | `one_shot_effects` | 378 | 10 | 10 | `absent` | `untested` |
| `residual.effect_clause.typed-spell-additional-cost-clause` | `one_shot_effects` | 70 | 9 | 9 | `absent` | `untested` |
| `residual.effect_clause.destroy-mass` | `one_shot_effects` | 128 | 8 | 8 | `absent` | `untested` |
| `residual.replacement.damage-prevention` | `replacement_prevention` | 115 | 8 | 8 | `absent` | `untested` |
| `residual.effect_clause.look-reveal` | `one_shot_effects` | 543 | 7 | 7 | `absent` | `untested` |
| `residual.effect_clause.put-counter` | `one_shot_effects` | 623 | 6 | 6 | `absent` | `untested` |
| `residual.activated_effect.life-change` | `one_shot_effects` | 111 | 5 | 5 | `absent` | `untested` |
| `residual.effect_clause.remove-counter` | `one_shot_effects` | 83 | 5 | 5 | `absent` | `untested` |
| `residual.effect_clause.tap-state` | `one_shot_effects` | 364 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.exile` | `one_shot_effects` | 221 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.return` | `one_shot_effects` | 171 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.put-onto-battlefield` | `one_shot_effects` | 143 | 4 | 4 | `absent` | `untested` |
| `residual.effect_clause.copy` | `one_shot_effects` | 86 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.destroy-target` | `one_shot_effects` | 37 | 4 | 4 | `absent` | `untested` |
| `residual.mechanic_dependency.cr-111-tokens` | `keyword_mechanics` | 16 | 4 | 4 | `absent` | `untested` |
| `residual.effect_clause.draw` | `one_shot_effects` | 548 | 3 | 3 | `absent` | `untested` |
| `residual.activated_effect.deal-damage` | `one_shot_effects` | 184 | 3 | 3 | `absent` | `untested` |
| `residual.activated_effect.create-token` | `one_shot_effects` | 163 | 3 | 3 | `absent` | `untested` |
| `residual.activated_effect.draw` | `one_shot_effects` | 150 | 3 | 3 | `absent` | `untested` |
| `residual.effect_clause.destroy` | `one_shot_effects` | 107 | 3 | 3 | `absent` | `untested` |
| `residual.activated_effect.copy` | `one_shot_effects` | 56 | 3 | 3 | `absent` | `untested` |

## Boundary

Inventory and classification are not implementation or trust. Universal systems remain conservatively below snapshot-complete until all required rules, pieces, rulings, and interactions close.
