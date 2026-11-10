---
title: "Reusable rules piece matrix"
status: "generated"
authoritative_source: "coverage/reusable-piece-matrix.json.gz"
verified: "b8601110d6121c761d17193dd9c4976fb5afc88457286c96736bbccd139b9b2a"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Reusable rules piece matrix

Current Oracle IR material ability and residual spans plus all registered capabilities, mechanics, handlers, components, and pinned rule references. This inventories current source relations without claiming universal runtime completion.

Counts official ruling presence by Oracle ID. Ruling prose is not yet behaviorally classified, so these counts are composition evidence rather than coverage claims.

## Snapshot

- Profile: `commander_review`
- Ontology: `reusable-pieces-v1`
- Pieces: 4,463
- Cards indexed: 31,623
- Material abilities classified: 59,927
- Unclassified material spans: 0
- Mapped pinned rules: 1,715 / 3,309
- Applicable piece pairs: 181,666
- Covered piece pairs: 1,941

## Ontology classes

| Class | Pieces |
|---|---:|
| `actions_permissions` — Actions, permissions, and prohibitions | 377 |
| `card_forms` — Card types and specialized forms | 12 |
| `choices_continuations` — Modes, targets, choices, and continuations | 23 |
| `combat` — Combat | 50 |
| `compiler_cardprogram` — Compiler and CardProgram pieces | 2,438 |
| `continuous_effects` — Static abilities and continuous effects | 159 |
| `costs_mana` — Costs and mana | 18 |
| `events_mutations` — Typed events and mutations | 173 |
| `keyword_mechanics` — Keyword actions and keyword abilities | 751 |
| `multiplayer_commander` — Multiplayer, Commander, and profile pieces | 6 |
| `object_identity` — Object identity and lifetime | 79 |
| `one_shot_effects` — One-shot semantic effects | 271 |
| `players_format` — Players, relationships, and format state | 3 |
| `proposals` — Casting and activation proposals | 63 |
| `quantities` — Quantity and value expressions | 2 |
| `references` — References | 1 |
| `replacement_prevention` — Replacement and prevention | 34 |
| `triggers` — Triggers | 3 |

## Universal systems

| System | Status | Pieces | Blocking pieces |
|---|---|---:|---:|
| `action_legality_casting_activation_costs_mana` | `inventoried` | 458 | 6 |
| `combat` | `compositional` | 50 | 0 |
| `derived_characteristics_static_layers` | `inventoried` | 159 | 7 |
| `generic_triggers_stack_placement` | `inventoried` | 3 | 3 |
| `multiplayer_player_leaving_commander` | `compositional` | 9 | 0 |
| `objects_identity_zones_faces_copies` | `inventoried` | 91 | 3 |
| `replacement_prevention` | `inventoried` | 34 | 4 |
| `state_turn_loops_stabilization` | `inventoried` | 0 | 0 |
| `targets_modes_searches_references_choices` | `inventoried` | 26 | 11 |
| `typed_transactions_events_mutations` | `inventoried` | 444 | 90 |

## Highest current blocker leverage

| Piece | Class | Residuals | Sole blockers | Expected cards | Runtime | Assurance |
|---|---|---:|---:|---:|---|---|
| `residual.continuous_layer.continuous-effect-layers-and-dependencies` | `continuous_effects` | 2,279 | 940 | 940 | `absent` | `untested` |
| `residual.effect_clause.unparsed-clause-grammar` | `one_shot_effects` | 2,511 | 167 | 167 | `absent` | `untested` |
| `residual.activated_effect.unparsed-clause-grammar` | `one_shot_effects` | 903 | 94 | 94 | `absent` | `untested` |
| `residual.effect_clause.create-token` | `one_shot_effects` | 775 | 36 | 36 | `absent` | `untested` |
| `residual.effect_clause.deal-damage` | `one_shot_effects` | 844 | 28 | 28 | `absent` | `untested` |
| `residual.effect_clause.return` | `one_shot_effects` | 842 | 26 | 26 | `absent` | `untested` |
| `residual.effect_clause.sacrifice` | `one_shot_effects` | 380 | 19 | 19 | `absent` | `untested` |
| `residual.effect_clause.life-change` | `one_shot_effects` | 646 | 11 | 11 | `absent` | `untested` |
| `residual.effect_clause.exile` | `one_shot_effects` | 966 | 10 | 10 | `absent` | `untested` |
| `residual.effect_clause.put-onto-battlefield` | `one_shot_effects` | 407 | 10 | 10 | `absent` | `untested` |
| `residual.effect_clause.typed-spell-additional-cost-clause` | `one_shot_effects` | 70 | 9 | 9 | `absent` | `untested` |
| `residual.effect_clause.destroy-mass` | `one_shot_effects` | 149 | 8 | 8 | `absent` | `untested` |
| `residual.replacement.damage-prevention` | `replacement_prevention` | 116 | 8 | 8 | `absent` | `untested` |
| `residual.effect_clause.put-counter` | `one_shot_effects` | 648 | 7 | 7 | `absent` | `untested` |
| `residual.effect_clause.draw` | `one_shot_effects` | 582 | 7 | 7 | `absent` | `untested` |
| `residual.effect_clause.look-reveal` | `one_shot_effects` | 559 | 7 | 7 | `absent` | `untested` |
| `residual.effect_clause.discard` | `one_shot_effects` | 214 | 6 | 6 | `absent` | `untested` |
| `residual.keyword_dependency.equip` | `keyword_mechanics` | 8 | 6 | 6 | `absent` | `untested` |
| `residual.effect_clause.tap-state` | `one_shot_effects` | 374 | 5 | 5 | `absent` | `untested` |
| `residual.activated_effect.draw` | `one_shot_effects` | 163 | 5 | 5 | `absent` | `untested` |
| `residual.activated_effect.life-change` | `one_shot_effects` | 128 | 5 | 5 | `absent` | `untested` |
| `residual.effect_clause.remove-counter` | `one_shot_effects` | 84 | 5 | 5 | `absent` | `untested` |
| `residual.activated_effect.exile` | `one_shot_effects` | 232 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.put-onto-battlefield` | `one_shot_effects` | 154 | 4 | 4 | `absent` | `untested` |
| `residual.effect_clause.copy` | `one_shot_effects` | 89 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.destroy-target` | `one_shot_effects` | 47 | 4 | 4 | `absent` | `untested` |
| `residual.mechanic_dependency.cr-111-tokens` | `keyword_mechanics` | 19 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.create-token` | `one_shot_effects` | 178 | 3 | 3 | `absent` | `untested` |
| `residual.activated_effect.return` | `one_shot_effects` | 176 | 3 | 3 | `absent` | `untested` |
| `residual.effect_clause.destroy` | `one_shot_effects` | 107 | 3 | 3 | `absent` | `untested` |

## Boundary

Inventory and classification are not implementation or trust. Universal systems remain conservatively below snapshot-complete until all required rules, pieces, rulings, and interactions close.
