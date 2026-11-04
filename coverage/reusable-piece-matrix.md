---
title: "Reusable rules piece matrix"
status: "generated"
authoritative_source: "coverage/reusable-piece-matrix.json.gz"
verified: "309ed62803c3f30e3146f97fc7513f288b7783a00de32110a0459a0ff6175f6e"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Reusable rules piece matrix

Current Oracle IR material ability and residual spans plus all registered capabilities, mechanics, handlers, components, and pinned rule references. This inventories current source relations without claiming universal runtime completion.

Counts official ruling presence by Oracle ID. Ruling prose is not yet behaviorally classified, so these counts are composition evidence rather than coverage claims.

## Snapshot

- Profile: `commander_review`
- Ontology: `reusable-pieces-v1`
- Pieces: 4,117
- Cards indexed: 31,623
- Material abilities classified: 60,031
- Unclassified material spans: 0
- Mapped pinned rules: 1,621 / 3,309
- Applicable piece pairs: 165,808
- Covered piece pairs: 1,941

## Ontology classes

| Class | Pieces |
|---|---:|
| `actions_permissions` — Actions, permissions, and prohibitions | 324 |
| `card_forms` — Card types and specialized forms | 12 |
| `choices_continuations` — Modes, targets, choices, and continuations | 22 |
| `combat` — Combat | 47 |
| `compiler_cardprogram` — Compiler and CardProgram pieces | 2,240 |
| `continuous_effects` — Static abilities and continuous effects | 148 |
| `costs_mana` — Costs and mana | 16 |
| `events_mutations` — Typed events and mutations | 167 |
| `keyword_mechanics` — Keyword actions and keyword abilities | 717 |
| `multiplayer_commander` — Multiplayer, Commander, and profile pieces | 6 |
| `object_identity` — Object identity and lifetime | 69 |
| `one_shot_effects` — One-shot semantic effects | 252 |
| `players_format` — Players, relationships, and format state | 3 |
| `proposals` — Casting and activation proposals | 55 |
| `quantities` — Quantity and value expressions | 2 |
| `references` — References | 1 |
| `replacement_prevention` — Replacement and prevention | 33 |
| `triggers` — Triggers | 3 |

## Universal systems

| System | Status | Pieces | Blocking pieces |
|---|---|---:|---:|
| `action_legality_casting_activation_costs_mana` | `inventoried` | 395 | 6 |
| `combat` | `compositional` | 47 | 0 |
| `derived_characteristics_static_layers` | `inventoried` | 148 | 7 |
| `generic_triggers_stack_placement` | `inventoried` | 3 | 3 |
| `multiplayer_player_leaving_commander` | `compositional` | 9 | 0 |
| `objects_identity_zones_faces_copies` | `inventoried` | 81 | 3 |
| `replacement_prevention` | `inventoried` | 33 | 4 |
| `state_turn_loops_stabilization` | `inventoried` | 0 | 0 |
| `targets_modes_searches_references_choices` | `inventoried` | 25 | 11 |
| `typed_transactions_events_mutations` | `inventoried` | 419 | 90 |

## Highest current blocker leverage

| Piece | Class | Residuals | Sole blockers | Expected cards | Runtime | Assurance |
|---|---|---:|---:|---:|---|---|
| `residual.continuous_layer.continuous-effect-layers-and-dependencies` | `continuous_effects` | 2,440 | 959 | 959 | `absent` | `untested` |
| `residual.effect_clause.unparsed-clause-grammar` | `one_shot_effects` | 2,739 | 219 | 219 | `absent` | `untested` |
| `residual.activated_effect.unparsed-clause-grammar` | `one_shot_effects` | 1,026 | 102 | 102 | `absent` | `untested` |
| `residual.effect_clause.create-token` | `one_shot_effects` | 916 | 72 | 72 | `absent` | `untested` |
| `residual.effect_clause.deal-damage` | `one_shot_effects` | 942 | 34 | 34 | `absent` | `untested` |
| `residual.effect_clause.return` | `one_shot_effects` | 878 | 24 | 24 | `absent` | `untested` |
| `residual.effect_clause.life-change` | `one_shot_effects` | 685 | 13 | 13 | `absent` | `untested` |
| `residual.effect_clause.tap-state` | `one_shot_effects` | 419 | 13 | 13 | `absent` | `untested` |
| `residual.effect_clause.sacrifice` | `one_shot_effects` | 376 | 12 | 12 | `absent` | `untested` |
| `residual.effect_clause.put-onto-battlefield` | `one_shot_effects` | 422 | 11 | 11 | `absent` | `untested` |
| `residual.effect_clause.typed-spell-additional-cost-clause` | `one_shot_effects` | 72 | 11 | 11 | `absent` | `untested` |
| `residual.effect_clause.exile` | `one_shot_effects` | 999 | 10 | 10 | `absent` | `untested` |
| `residual.effect_clause.put-counter` | `one_shot_effects` | 663 | 9 | 9 | `absent` | `untested` |
| `residual.effect_clause.draw` | `one_shot_effects` | 633 | 9 | 9 | `absent` | `untested` |
| `residual.keyword_dependency.assist` | `keyword_mechanics` | 16 | 9 | 9 | `absent` | `untested` |
| `residual.activated_effect.create-token` | `one_shot_effects` | 199 | 8 | 8 | `absent` | `untested` |
| `residual.effect_clause.destroy-mass` | `one_shot_effects` | 165 | 8 | 8 | `absent` | `untested` |
| `residual.effect_clause.look-reveal` | `one_shot_effects` | 570 | 7 | 7 | `absent` | `untested` |
| `residual.replacement.damage-prevention` | `replacement_prevention` | 119 | 7 | 7 | `absent` | `untested` |
| `residual.keyword_dependency.phasing` | `keyword_mechanics` | 12 | 6 | 6 | `absent` | `untested` |
| `residual.keyword_dependency.equip` | `keyword_mechanics` | 8 | 6 | 6 | `absent` | `untested` |
| `residual.effect_clause.discard` | `one_shot_effects` | 220 | 5 | 5 | `absent` | `untested` |
| `residual.activated_effect.draw` | `one_shot_effects` | 193 | 5 | 5 | `absent` | `untested` |
| `residual.activated_effect.life-change` | `one_shot_effects` | 134 | 5 | 5 | `absent` | `untested` |
| `residual.effect_clause.remove-counter` | `one_shot_effects` | 85 | 5 | 5 | `absent` | `untested` |
| `residual.activated_effect.exile` | `one_shot_effects` | 239 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.put-onto-battlefield` | `one_shot_effects` | 160 | 4 | 4 | `absent` | `untested` |
| `residual.effect_clause.copy` | `one_shot_effects` | 90 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.destroy-target` | `one_shot_effects` | 51 | 4 | 4 | `absent` | `untested` |
| `residual.mechanic_dependency.cr-111-tokens` | `keyword_mechanics` | 14 | 4 | 4 | `absent` | `untested` |

## Boundary

Inventory and classification are not implementation or trust. Universal systems remain conservatively below snapshot-complete until all required rules, pieces, rulings, and interactions close.
