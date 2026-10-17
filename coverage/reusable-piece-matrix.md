---
title: "Reusable rules piece matrix"
status: "generated"
authoritative_source: "coverage/reusable-piece-matrix.json.gz"
verified: "302cf6de52d1a4d32dc598efff01898caa43e8cb655d6c648b4512b00fa2ff22"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Reusable rules piece matrix

Current Oracle IR material ability and residual spans plus all registered capabilities, mechanics, handlers, components, and pinned rule references. This inventories current source relations without claiming universal runtime completion.

Counts official ruling presence by Oracle ID. Ruling prose is not yet behaviorally classified, so these counts are composition evidence rather than coverage claims.

## Snapshot

- Profile: `commander_review`
- Ontology: `reusable-pieces-v1`
- Pieces: 3,368
- Cards indexed: 31,623
- Material abilities classified: 59,950
- Unclassified material spans: 0
- Mapped pinned rules: 1,289 / 3,309
- Applicable piece pairs: 125,708
- Covered piece pairs: 1,312

## Ontology classes

| Class | Pieces |
|---|---:|
| `actions_permissions` — Actions, permissions, and prohibitions | 166 |
| `card_forms` — Card types and specialized forms | 8 |
| `choices_continuations` — Modes, targets, choices, and continuations | 15 |
| `combat` — Combat | 35 |
| `compiler_cardprogram` — Compiler and CardProgram pieces | 1,914 |
| `continuous_effects` — Static abilities and continuous effects | 87 |
| `costs_mana` — Costs and mana | 13 |
| `events_mutations` — Typed events and mutations | 137 |
| `keyword_mechanics` — Keyword actions and keyword abilities | 652 |
| `multiplayer_commander` — Multiplayer, Commander, and profile pieces | 5 |
| `object_identity` — Object identity and lifetime | 50 |
| `one_shot_effects` — One-shot semantic effects | 207 |
| `players_format` — Players, relationships, and format state | 3 |
| `proposals` — Casting and activation proposals | 42 |
| `quantities` — Quantity and value expressions | 2 |
| `references` — References | 1 |
| `replacement_prevention` — Replacement and prevention | 28 |
| `triggers` — Triggers | 3 |

## Universal systems

| System | Status | Pieces | Blocking pieces |
|---|---|---:|---:|
| `action_legality_casting_activation_costs_mana` | `inventoried` | 221 | 6 |
| `combat` | `compositional` | 35 | 0 |
| `derived_characteristics_static_layers` | `inventoried` | 87 | 7 |
| `generic_triggers_stack_placement` | `inventoried` | 3 | 3 |
| `multiplayer_player_leaving_commander` | `compositional` | 8 | 0 |
| `objects_identity_zones_faces_copies` | `inventoried` | 58 | 1 |
| `replacement_prevention` | `inventoried` | 28 | 4 |
| `state_turn_loops_stabilization` | `inventoried` | 0 | 0 |
| `targets_modes_searches_references_choices` | `inventoried` | 18 | 11 |
| `typed_transactions_events_mutations` | `inventoried` | 344 | 88 |

## Highest current blocker leverage

| Piece | Class | Residuals | Sole blockers | Expected cards | Runtime | Assurance |
|---|---|---:|---:|---:|---|---|
| `residual.continuous_layer.continuous-effect-layers-and-dependencies` | `continuous_effects` | 3,248 | 1,492 | 1,492 | `absent` | `untested` |
| `residual.activated_effect.unparsed-clause-grammar` | `one_shot_effects` | 1,161 | 112 | 112 | `absent` | `untested` |
| `residual.effect_clause.unparsed-clause-grammar` | `one_shot_effects` | 1,732 | 58 | 58 | `absent` | `untested` |
| `residual.effect_clause.typed-spell-additional-cost-clause` | `one_shot_effects` | 102 | 20 | 20 | `absent` | `untested` |
| `residual.keyword_dependency.banding` | `keyword_mechanics` | 24 | 19 | 19 | `absent` | `untested` |
| `residual.replacement.damage-prevention` | `replacement_prevention` | 124 | 14 | 14 | `absent` | `untested` |
| `residual.keyword_dependency.cipher` | `keyword_mechanics` | 15 | 11 | 11 | `absent` | `untested` |
| `residual.activated_effect.create-token` | `one_shot_effects` | 233 | 10 | 10 | `absent` | `untested` |
| `residual.mechanic_dependency.morph-unsupported-cost` | `keyword_mechanics` | 14 | 10 | 10 | `absent` | `untested` |
| `residual.keyword_dependency.assist` | `keyword_mechanics` | 16 | 9 | 9 | `absent` | `untested` |
| `residual.effect_clause.return` | `one_shot_effects` | 434 | 8 | 8 | `absent` | `untested` |
| `residual.activated_effect.exile` | `one_shot_effects` | 260 | 8 | 8 | `absent` | `untested` |
| `residual.keyword_dependency.fuse` | `keyword_mechanics` | 34 | 8 | 8 | `absent` | `untested` |
| `residual.keyword_dependency.conspire` | `keyword_mechanics` | 11 | 7 | 7 | `absent` | `untested` |
| `residual.effect_clause.create-token` | `one_shot_effects` | 429 | 6 | 6 | `absent` | `untested` |
| `residual.effect_clause.life-change` | `one_shot_effects` | 360 | 6 | 6 | `absent` | `untested` |
| `residual.activated_effect.life-change` | `one_shot_effects` | 151 | 6 | 6 | `absent` | `untested` |
| `residual.effect_clause.destroy-mass` | `one_shot_effects` | 137 | 6 | 6 | `absent` | `untested` |
| `residual.keyword_dependency.phasing` | `keyword_mechanics` | 12 | 6 | 6 | `absent` | `untested` |
| `residual.keyword_dependency.provoke` | `keyword_mechanics` | 8 | 6 | 6 | `absent` | `untested` |
| `residual.target_or_choice.typed-enchant-restriction` | `choices_continuations` | 7 | 6 | 6 | `absent` | `untested` |
| `residual.effect_clause.exile` | `one_shot_effects` | 477 | 5 | 5 | `absent` | `untested` |
| `residual.effect_clause.counter` | `one_shot_effects` | 154 | 5 | 5 | `absent` | `untested` |
| `residual.activated_effect.destroy-target` | `one_shot_effects` | 54 | 5 | 5 | `absent` | `untested` |
| `residual.keyword_dependency.increment` | `keyword_mechanics` | 9 | 5 | 5 | `absent` | `untested` |
| `residual.effect_clause.destroy-target` | `one_shot_effects` | 272 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.draw` | `one_shot_effects` | 221 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.return` | `one_shot_effects` | 203 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.put-onto-battlefield` | `one_shot_effects` | 162 | 4 | 4 | `absent` | `untested` |
| `residual.keyword_dependency.equip` | `keyword_mechanics` | 8 | 4 | 4 | `absent` | `untested` |

## Boundary

Inventory and classification are not implementation or trust. Universal systems remain conservatively below snapshot-complete until all required rules, pieces, rulings, and interactions close.
