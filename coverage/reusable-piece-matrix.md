---
title: "Reusable rules piece matrix"
status: "generated"
authoritative_source: "coverage/reusable-piece-matrix.json.gz"
verified: "d6a24739c1b027e09ee8e368161c836a1c2cd8a2c8cfa8bfccd53c2621d7bf64"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Reusable rules piece matrix

Current Oracle IR material ability and residual spans plus all registered capabilities, mechanics, handlers, components, and pinned rule references. This inventories current source relations without claiming universal runtime completion.

Counts official ruling presence by Oracle ID. Ruling prose is not yet behaviorally classified, so these counts are composition evidence rather than coverage claims.

## Snapshot

- Profile: `commander_review`
- Ontology: `reusable-pieces-v1`
- Pieces: 2,372
- Cards indexed: 31,623
- Material abilities classified: 59,482
- Unclassified material spans: 0
- Mapped pinned rules: 980 / 3,309
- Applicable piece pairs: 72,122
- Covered piece pairs: 919

## Ontology classes

| Class | Pieces |
|---|---:|
| `actions_permissions` — Actions, permissions, and prohibitions | 85 |
| `card_forms` — Card types and specialized forms | 6 |
| `choices_continuations` — Modes, targets, choices, and continuations | 14 |
| `combat` — Combat | 24 |
| `compiler_cardprogram` — Compiler and CardProgram pieces | 1,219 |
| `continuous_effects` — Static abilities and continuous effects | 50 |
| `costs_mana` — Costs and mana | 9 |
| `events_mutations` — Typed events and mutations | 113 |
| `keyword_mechanics` — Keyword actions and keyword abilities | 577 |
| `multiplayer_commander` — Multiplayer, Commander, and profile pieces | 5 |
| `object_identity` — Object identity and lifetime | 32 |
| `one_shot_effects` — One-shot semantic effects | 183 |
| `players_format` — Players, relationships, and format state | 2 |
| `proposals` — Casting and activation proposals | 26 |
| `quantities` — Quantity and value expressions | 1 |
| `references` — References | 1 |
| `replacement_prevention` — Replacement and prevention | 22 |
| `triggers` — Triggers | 3 |

## Universal systems

| System | Status | Pieces | Blocking pieces |
|---|---|---:|---:|
| `action_legality_casting_activation_costs_mana` | `inventoried` | 120 | 6 |
| `combat` | `compositional` | 24 | 0 |
| `derived_characteristics_static_layers` | `inventoried` | 50 | 7 |
| `generic_triggers_stack_placement` | `inventoried` | 3 | 3 |
| `multiplayer_player_leaving_commander` | `compositional` | 7 | 0 |
| `objects_identity_zones_faces_copies` | `inventoried` | 38 | 1 |
| `replacement_prevention` | `inventoried` | 22 | 4 |
| `state_turn_loops_stabilization` | `inventoried` | 0 | 0 |
| `targets_modes_searches_references_choices` | `inventoried` | 16 | 10 |
| `typed_transactions_events_mutations` | `inventoried` | 296 | 82 |

## Highest current blocker leverage

| Piece | Class | Residuals | Sole blockers | Expected cards | Runtime | Assurance |
|---|---|---:|---:|---:|---|---|
| `residual.continuous_layer.continuous-effect-layers-and-dependencies` | `continuous_effects` | 5,226 | 2,268 | 2,268 | `absent` | `untested` |
| `residual.effect_clause.unparsed-clause-grammar` | `one_shot_effects` | 2,287 | 195 | 195 | `absent` | `untested` |
| `residual.activated_effect.unparsed-clause-grammar` | `one_shot_effects` | 1,826 | 126 | 126 | `absent` | `untested` |
| `residual.keyword_dependency.banding` | `keyword_mechanics` | 24 | 19 | 19 | `absent` | `untested` |
| `residual.effect_clause.typed-spell-additional-cost-clause` | `one_shot_effects` | 106 | 18 | 18 | `absent` | `untested` |
| `residual.effect_clause.life-change` | `one_shot_effects` | 479 | 16 | 16 | `absent` | `untested` |
| `residual.activated_effect.put-onto-battlefield` | `one_shot_effects` | 221 | 15 | 15 | `absent` | `untested` |
| `residual.effect_clause.exile` | `one_shot_effects` | 552 | 14 | 14 | `absent` | `untested` |
| `residual.keyword_dependency.start-your-engines` | `keyword_mechanics` | 40 | 14 | 14 | `absent` | `untested` |
| `residual.effect_clause.create-token` | `one_shot_effects` | 544 | 13 | 13 | `absent` | `untested` |
| `residual.replacement.damage-prevention` | `replacement_prevention` | 140 | 13 | 13 | `absent` | `untested` |
| `residual.activated_effect.create-token` | `one_shot_effects` | 283 | 12 | 12 | `absent` | `untested` |
| `residual.keyword_dependency.equip` | `keyword_mechanics` | 25 | 12 | 12 | `absent` | `untested` |
| `residual.keyword_dependency.living-weapon` | `keyword_mechanics` | 19 | 12 | 12 | `absent` | `untested` |
| `residual.effect_clause.return` | `one_shot_effects` | 568 | 11 | 11 | `absent` | `untested` |
| `residual.effect_clause.sacrifice` | `one_shot_effects` | 110 | 11 | 11 | `absent` | `untested` |
| `residual.keyword_dependency.rebound` | `keyword_mechanics` | 34 | 11 | 11 | `absent` | `untested` |
| `residual.keyword_dependency.myriad` | `keyword_mechanics` | 23 | 11 | 11 | `absent` | `untested` |
| `residual.keyword_dependency.umbra-armor` | `keyword_mechanics` | 15 | 10 | 10 | `absent` | `untested` |
| `residual.activated_effect.life-change` | `one_shot_effects` | 182 | 9 | 9 | `absent` | `untested` |
| `residual.keyword_dependency.for-mirrodin` | `keyword_mechanics` | 13 | 9 | 9 | `absent` | `untested` |
| `residual.effect_clause.add-mana` | `one_shot_effects` | 57 | 8 | 8 | `absent` | `untested` |
| `residual.keyword_dependency.split-second` | `keyword_mechanics` | 21 | 8 | 8 | `absent` | `untested` |
| `residual.keyword_dependency.extort` | `keyword_mechanics` | 18 | 8 | 8 | `absent` | `untested` |
| `residual.keyword_dependency.enlist` | `keyword_mechanics` | 12 | 8 | 8 | `absent` | `untested` |
| `residual.effect_clause.destroy-mass` | `one_shot_effects` | 141 | 7 | 7 | `absent` | `untested` |
| `residual.keyword_dependency.assist` | `keyword_mechanics` | 16 | 7 | 7 | `absent` | `untested` |
| `residual.keyword_dependency.learn` | `keyword_mechanics` | 13 | 7 | 7 | `absent` | `untested` |
| `residual.activated_effect.return` | `one_shot_effects` | 331 | 6 | 6 | `absent` | `untested` |
| `residual.activated_effect.exile` | `one_shot_effects` | 291 | 6 | 6 | `absent` | `untested` |

## Boundary

Inventory and classification are not implementation or trust. Universal systems remain conservatively below snapshot-complete until all required rules, pieces, rulings, and interactions close.
