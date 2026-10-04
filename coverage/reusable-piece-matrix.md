---
title: "Reusable rules piece matrix"
status: "generated"
authoritative_source: "coverage/reusable-piece-matrix.json.gz"
verified: "57d7d091c453dbd87f834a41c40ac1905d62a2c2bacd166eb1d9267973e31e53"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Reusable rules piece matrix

Current Oracle IR material ability and residual spans plus all registered capabilities, mechanics, handlers, components, and pinned rule references. This inventories current source relations without claiming universal runtime completion.

Counts official ruling presence by Oracle ID. Ruling prose is not yet behaviorally classified, so these counts are composition evidence rather than coverage claims.

## Snapshot

- Profile: `commander_review`
- Ontology: `reusable-pieces-v1`
- Pieces: 2,858
- Cards indexed: 31,623
- Material abilities classified: 59,361
- Unclassified material spans: 0
- Mapped pinned rules: 1,081 / 3,309
- Applicable piece pairs: 95,804
- Covered piece pairs: 1,035

## Ontology classes

| Class | Pieces |
|---|---:|
| `actions_permissions` — Actions, permissions, and prohibitions | 113 |
| `card_forms` — Card types and specialized forms | 8 |
| `choices_continuations` — Modes, targets, choices, and continuations | 14 |
| `combat` — Combat | 26 |
| `compiler_cardprogram` — Compiler and CardProgram pieces | 1,595 |
| `continuous_effects` — Static abilities and continuous effects | 54 |
| `costs_mana` — Costs and mana | 9 |
| `events_mutations` — Typed events and mutations | 121 |
| `keyword_mechanics` — Keyword actions and keyword abilities | 612 |
| `multiplayer_commander` — Multiplayer, Commander, and profile pieces | 5 |
| `object_identity` — Object identity and lifetime | 40 |
| `one_shot_effects` — One-shot semantic effects | 196 |
| `players_format` — Players, relationships, and format state | 2 |
| `proposals` — Casting and activation proposals | 33 |
| `quantities` — Quantity and value expressions | 2 |
| `references` — References | 1 |
| `replacement_prevention` — Replacement and prevention | 24 |
| `triggers` — Triggers | 3 |

## Universal systems

| System | Status | Pieces | Blocking pieces |
|---|---|---:|---:|
| `action_legality_casting_activation_costs_mana` | `inventoried` | 155 | 6 |
| `combat` | `compositional` | 26 | 0 |
| `derived_characteristics_static_layers` | `inventoried` | 54 | 7 |
| `generic_triggers_stack_placement` | `inventoried` | 3 | 3 |
| `multiplayer_player_leaving_commander` | `compositional` | 7 | 0 |
| `objects_identity_zones_faces_copies` | `inventoried` | 48 | 1 |
| `replacement_prevention` | `inventoried` | 24 | 4 |
| `state_turn_loops_stabilization` | `inventoried` | 0 | 0 |
| `targets_modes_searches_references_choices` | `inventoried` | 17 | 11 |
| `typed_transactions_events_mutations` | `inventoried` | 317 | 87 |

## Highest current blocker leverage

| Piece | Class | Residuals | Sole blockers | Expected cards | Runtime | Assurance |
|---|---|---:|---:|---:|---|---|
| `residual.continuous_layer.continuous-effect-layers-and-dependencies` | `continuous_effects` | 4,047 | 1,866 | 1,866 | `absent` | `untested` |
| `residual.effect_clause.unparsed-clause-grammar` | `one_shot_effects` | 1,862 | 178 | 178 | `absent` | `untested` |
| `residual.activated_effect.unparsed-clause-grammar` | `one_shot_effects` | 1,406 | 136 | 136 | `absent` | `untested` |
| `residual.effect_clause.typed-spell-additional-cost-clause` | `one_shot_effects` | 106 | 22 | 22 | `absent` | `untested` |
| `residual.keyword_dependency.banding` | `keyword_mechanics` | 24 | 19 | 19 | `absent` | `untested` |
| `residual.keyword_dependency.start-your-engines` | `keyword_mechanics` | 40 | 16 | 16 | `absent` | `untested` |
| `residual.replacement.damage-prevention` | `replacement_prevention` | 132 | 15 | 15 | `absent` | `untested` |
| `residual.activated_effect.create-token` | `one_shot_effects` | 266 | 13 | 13 | `absent` | `untested` |
| `residual.mechanic_dependency.fading-remaining-lifecycle` | `keyword_mechanics` | 17 | 11 | 11 | `absent` | `untested` |
| `residual.keyword_dependency.umbra-armor` | `keyword_mechanics` | 15 | 11 | 11 | `absent` | `untested` |
| `residual.keyword_dependency.extort` | `keyword_mechanics` | 18 | 10 | 10 | `absent` | `untested` |
| `residual.activated_effect.life-change` | `one_shot_effects` | 171 | 9 | 9 | `absent` | `untested` |
| `residual.keyword_dependency.assist` | `keyword_mechanics` | 16 | 9 | 9 | `absent` | `untested` |
| `residual.keyword_dependency.cipher` | `keyword_mechanics` | 15 | 9 | 9 | `absent` | `untested` |
| `residual.keyword_dependency.learn` | `keyword_mechanics` | 13 | 9 | 9 | `absent` | `untested` |
| `residual.keyword_dependency.enlist` | `keyword_mechanics` | 12 | 9 | 9 | `absent` | `untested` |
| `residual.effect_clause.create-token` | `one_shot_effects` | 509 | 8 | 8 | `absent` | `untested` |
| `residual.effect_clause.return` | `one_shot_effects` | 447 | 8 | 8 | `absent` | `untested` |
| `residual.activated_effect.exile` | `one_shot_effects` | 269 | 8 | 8 | `absent` | `untested` |
| `residual.effect_clause.add-mana` | `one_shot_effects` | 57 | 8 | 8 | `absent` | `untested` |
| `residual.keyword_dependency.aftermath` | `keyword_mechanics` | 27 | 8 | 8 | `absent` | `untested` |
| `residual.keyword_dependency.split-second` | `keyword_mechanics` | 21 | 8 | 8 | `absent` | `untested` |
| `residual.mechanic_dependency.morph-unsupported-cost` | `keyword_mechanics` | 14 | 8 | 8 | `absent` | `untested` |
| `residual.mechanic_dependency.graft-remaining-lifecycle` | `keyword_mechanics` | 13 | 8 | 8 | `absent` | `untested` |
| `residual.effect_clause.exile` | `one_shot_effects` | 486 | 7 | 7 | `absent` | `untested` |
| `residual.effect_clause.life-change` | `one_shot_effects` | 412 | 7 | 7 | `absent` | `untested` |
| `residual.keyword_dependency.fuse` | `keyword_mechanics` | 34 | 7 | 7 | `absent` | `untested` |
| `residual.mechanic_dependency.vanishing-remaining-lifecycle` | `keyword_mechanics` | 20 | 7 | 7 | `absent` | `untested` |
| `residual.effect_clause.counter` | `one_shot_effects` | 174 | 6 | 6 | `absent` | `untested` |
| `residual.effect_clause.destroy-mass` | `one_shot_effects` | 138 | 6 | 6 | `absent` | `untested` |

## Boundary

Inventory and classification are not implementation or trust. Universal systems remain conservatively below snapshot-complete until all required rules, pieces, rulings, and interactions close.
