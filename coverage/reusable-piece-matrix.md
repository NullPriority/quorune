---
title: "Reusable rules piece matrix"
status: "generated"
authoritative_source: "coverage/reusable-piece-matrix.json.gz"
verified: "7cb615270e86179c30dec3786da04714f6be846c99999ff0624ff9fa61f594f8"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Reusable rules piece matrix

Current Oracle IR material ability and residual spans plus all registered capabilities, mechanics, handlers, components, and pinned rule references. This inventories current source relations without claiming universal runtime completion.

Counts official ruling presence by Oracle ID. Ruling prose is not yet behaviorally classified, so these counts are composition evidence rather than coverage claims.

## Snapshot

- Profile: `commander_review`
- Ontology: `reusable-pieces-v1`
- Pieces: 3,145
- Cards indexed: 31,623
- Material abilities classified: 59,880
- Unclassified material spans: 0
- Mapped pinned rules: 1,180 / 3,309
- Applicable piece pairs: 113,045
- Covered piece pairs: 1,193

## Ontology classes

| Class | Pieces |
|---|---:|
| `actions_permissions` — Actions, permissions, and prohibitions | 136 |
| `card_forms` — Card types and specialized forms | 8 |
| `choices_continuations` — Modes, targets, choices, and continuations | 15 |
| `combat` — Combat | 32 |
| `compiler_cardprogram` — Compiler and CardProgram pieces | 1,794 |
| `continuous_effects` — Static abilities and continuous effects | 76 |
| `costs_mana` — Costs and mana | 11 |
| `events_mutations` — Typed events and mutations | 126 |
| `keyword_mechanics` — Keyword actions and keyword abilities | 626 |
| `multiplayer_commander` — Multiplayer, Commander, and profile pieces | 5 |
| `object_identity` — Object identity and lifetime | 43 |
| `one_shot_effects` — One-shot semantic effects | 202 |
| `players_format` — Players, relationships, and format state | 3 |
| `proposals` — Casting and activation proposals | 37 |
| `quantities` — Quantity and value expressions | 2 |
| `references` — References | 1 |
| `replacement_prevention` — Replacement and prevention | 25 |
| `triggers` — Triggers | 3 |

## Universal systems

| System | Status | Pieces | Blocking pieces |
|---|---|---:|---:|
| `action_legality_casting_activation_costs_mana` | `inventoried` | 184 | 6 |
| `combat` | `compositional` | 32 | 0 |
| `derived_characteristics_static_layers` | `inventoried` | 76 | 7 |
| `generic_triggers_stack_placement` | `inventoried` | 3 | 3 |
| `multiplayer_player_leaving_commander` | `compositional` | 8 | 0 |
| `objects_identity_zones_faces_copies` | `inventoried` | 51 | 1 |
| `replacement_prevention` | `inventoried` | 25 | 4 |
| `state_turn_loops_stabilization` | `inventoried` | 0 | 0 |
| `targets_modes_searches_references_choices` | `inventoried` | 18 | 11 |
| `typed_transactions_events_mutations` | `inventoried` | 328 | 88 |

## Highest current blocker leverage

| Piece | Class | Residuals | Sole blockers | Expected cards | Runtime | Assurance |
|---|---|---:|---:|---:|---|---|
| `residual.continuous_layer.continuous-effect-layers-and-dependencies` | `continuous_effects` | 3,484 | 1,628 | 1,628 | `absent` | `untested` |
| `residual.activated_effect.unparsed-clause-grammar` | `one_shot_effects` | 1,185 | 113 | 113 | `absent` | `untested` |
| `residual.effect_clause.unparsed-clause-grammar` | `one_shot_effects` | 1,773 | 62 | 62 | `absent` | `untested` |
| `residual.effect_clause.typed-spell-additional-cost-clause` | `one_shot_effects` | 102 | 20 | 20 | `absent` | `untested` |
| `residual.keyword_dependency.banding` | `keyword_mechanics` | 24 | 19 | 19 | `absent` | `untested` |
| `residual.replacement.damage-prevention` | `replacement_prevention` | 126 | 14 | 14 | `absent` | `untested` |
| `residual.activated_effect.create-token` | `one_shot_effects` | 238 | 10 | 10 | `absent` | `untested` |
| `residual.keyword_dependency.cipher` | `keyword_mechanics` | 15 | 10 | 10 | `absent` | `untested` |
| `residual.keyword_dependency.assist` | `keyword_mechanics` | 16 | 9 | 9 | `absent` | `untested` |
| `residual.mechanic_dependency.morph-unsupported-cost` | `keyword_mechanics` | 14 | 9 | 9 | `absent` | `untested` |
| `residual.keyword_dependency.learn` | `keyword_mechanics` | 13 | 9 | 9 | `absent` | `untested` |
| `residual.effect_clause.return` | `one_shot_effects` | 437 | 8 | 8 | `absent` | `untested` |
| `residual.activated_effect.exile` | `one_shot_effects` | 265 | 8 | 8 | `absent` | `untested` |
| `residual.activated_effect.destroy-target` | `one_shot_effects` | 58 | 7 | 7 | `absent` | `untested` |
| `residual.keyword_dependency.fuse` | `keyword_mechanics` | 34 | 7 | 7 | `absent` | `untested` |
| `residual.keyword_dependency.conspire` | `keyword_mechanics` | 11 | 7 | 7 | `absent` | `untested` |
| `residual.effect_clause.create-token` | `one_shot_effects` | 454 | 6 | 6 | `absent` | `untested` |
| `residual.effect_clause.life-change` | `one_shot_effects` | 382 | 6 | 6 | `absent` | `untested` |
| `residual.activated_effect.life-change` | `one_shot_effects` | 158 | 6 | 6 | `absent` | `untested` |
| `residual.effect_clause.destroy-mass` | `one_shot_effects` | 137 | 6 | 6 | `absent` | `untested` |
| `residual.keyword_dependency.phasing` | `keyword_mechanics` | 12 | 6 | 6 | `absent` | `untested` |
| `residual.keyword_dependency.provoke` | `keyword_mechanics` | 8 | 6 | 6 | `absent` | `untested` |
| `residual.target_or_choice.typed-enchant-restriction` | `choices_continuations` | 7 | 6 | 6 | `absent` | `untested` |
| `residual.effect_clause.exile` | `one_shot_effects` | 481 | 5 | 5 | `absent` | `untested` |
| `residual.effect_clause.counter` | `one_shot_effects` | 160 | 5 | 5 | `absent` | `untested` |
| `residual.keyword_dependency.increment` | `keyword_mechanics` | 9 | 5 | 5 | `absent` | `untested` |
| `residual.effect_clause.destroy-target` | `one_shot_effects` | 288 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.draw` | `one_shot_effects` | 230 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.return` | `one_shot_effects` | 204 | 4 | 4 | `absent` | `untested` |
| `residual.keyword_dependency.equip` | `keyword_mechanics` | 8 | 4 | 4 | `absent` | `untested` |

## Boundary

Inventory and classification are not implementation or trust. Universal systems remain conservatively below snapshot-complete until all required rules, pieces, rulings, and interactions close.
