---
title: "Reusable rules piece matrix"
status: "generated"
authoritative_source: "coverage/reusable-piece-matrix.json.gz"
verified: "d552fb1909d71973ff2cfc85bc75f3f8ab6605172f4261329bb23e9140506d47"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Reusable rules piece matrix

Current Oracle IR material ability and residual spans plus all registered capabilities, mechanics, handlers, components, and pinned rule references. This inventories current source relations without claiming universal runtime completion.

Counts official ruling presence by Oracle ID. Ruling prose is not yet behaviorally classified, so these counts are composition evidence rather than coverage claims.

## Snapshot

- Profile: `commander_review`
- Ontology: `reusable-pieces-v1`
- Pieces: 3,022
- Cards indexed: 31,623
- Material abilities classified: 59,855
- Unclassified material spans: 0
- Mapped pinned rules: 1,120 / 3,309
- Applicable piece pairs: 106,080
- Covered piece pairs: 1,075

## Ontology classes

| Class | Pieces |
|---|---:|
| `actions_permissions` — Actions, permissions, and prohibitions | 125 |
| `card_forms` — Card types and specialized forms | 8 |
| `choices_continuations` — Modes, targets, choices, and continuations | 14 |
| `combat` — Combat | 30 |
| `compiler_cardprogram` — Compiler and CardProgram pieces | 1,706 |
| `continuous_effects` — Static abilities and continuous effects | 67 |
| `costs_mana` — Costs and mana | 10 |
| `events_mutations` — Typed events and mutations | 125 |
| `keyword_mechanics` — Keyword actions and keyword abilities | 621 |
| `multiplayer_commander` — Multiplayer, Commander, and profile pieces | 5 |
| `object_identity` — Object identity and lifetime | 43 |
| `one_shot_effects` — One-shot semantic effects | 200 |
| `players_format` — Players, relationships, and format state | 2 |
| `proposals` — Casting and activation proposals | 35 |
| `quantities` — Quantity and value expressions | 2 |
| `references` — References | 1 |
| `replacement_prevention` — Replacement and prevention | 25 |
| `triggers` — Triggers | 3 |

## Universal systems

| System | Status | Pieces | Blocking pieces |
|---|---|---:|---:|
| `action_legality_casting_activation_costs_mana` | `inventoried` | 170 | 6 |
| `combat` | `compositional` | 30 | 0 |
| `derived_characteristics_static_layers` | `inventoried` | 67 | 7 |
| `generic_triggers_stack_placement` | `inventoried` | 3 | 3 |
| `multiplayer_player_leaving_commander` | `compositional` | 7 | 0 |
| `objects_identity_zones_faces_copies` | `inventoried` | 51 | 1 |
| `replacement_prevention` | `inventoried` | 25 | 4 |
| `state_turn_loops_stabilization` | `inventoried` | 0 | 0 |
| `targets_modes_searches_references_choices` | `inventoried` | 17 | 11 |
| `typed_transactions_events_mutations` | `inventoried` | 325 | 88 |

## Highest current blocker leverage

| Piece | Class | Residuals | Sole blockers | Expected cards | Runtime | Assurance |
|---|---|---:|---:|---:|---|---|
| `residual.continuous_layer.continuous-effect-layers-and-dependencies` | `continuous_effects` | 3,772 | 1,791 | 1,791 | `absent` | `untested` |
| `residual.activated_effect.unparsed-clause-grammar` | `one_shot_effects` | 1,352 | 144 | 144 | `absent` | `untested` |
| `residual.effect_clause.unparsed-clause-grammar` | `one_shot_effects` | 1,843 | 69 | 69 | `absent` | `untested` |
| `residual.effect_clause.typed-spell-additional-cost-clause` | `one_shot_effects` | 106 | 22 | 22 | `absent` | `untested` |
| `residual.keyword_dependency.banding` | `keyword_mechanics` | 24 | 19 | 19 | `absent` | `untested` |
| `residual.replacement.damage-prevention` | `replacement_prevention` | 131 | 18 | 18 | `absent` | `untested` |
| `residual.keyword_dependency.start-your-engines` | `keyword_mechanics` | 40 | 16 | 16 | `absent` | `untested` |
| `residual.activated_effect.create-token` | `one_shot_effects` | 243 | 10 | 10 | `absent` | `untested` |
| `residual.keyword_dependency.extort` | `keyword_mechanics` | 18 | 10 | 10 | `absent` | `untested` |
| `residual.keyword_dependency.cipher` | `keyword_mechanics` | 15 | 10 | 10 | `absent` | `untested` |
| `residual.activated_effect.life-change` | `one_shot_effects` | 167 | 9 | 9 | `absent` | `untested` |
| `residual.keyword_dependency.split-second` | `keyword_mechanics` | 21 | 9 | 9 | `absent` | `untested` |
| `residual.keyword_dependency.assist` | `keyword_mechanics` | 16 | 9 | 9 | `absent` | `untested` |
| `residual.mechanic_dependency.morph-unsupported-cost` | `keyword_mechanics` | 14 | 9 | 9 | `absent` | `untested` |
| `residual.keyword_dependency.learn` | `keyword_mechanics` | 13 | 9 | 9 | `absent` | `untested` |
| `residual.mechanic_dependency.graft-remaining-lifecycle` | `keyword_mechanics` | 13 | 9 | 9 | `absent` | `untested` |
| `residual.keyword_dependency.enlist` | `keyword_mechanics` | 12 | 9 | 9 | `absent` | `untested` |
| `residual.effect_clause.return` | `one_shot_effects` | 438 | 8 | 8 | `absent` | `untested` |
| `residual.activated_effect.exile` | `one_shot_effects` | 266 | 8 | 8 | `absent` | `untested` |
| `residual.keyword_dependency.aftermath` | `keyword_mechanics` | 27 | 8 | 8 | `absent` | `untested` |
| `residual.activated_effect.destroy-target` | `one_shot_effects` | 58 | 7 | 7 | `absent` | `untested` |
| `residual.effect_clause.add-mana` | `one_shot_effects` | 53 | 7 | 7 | `absent` | `untested` |
| `residual.keyword_dependency.fuse` | `keyword_mechanics` | 34 | 7 | 7 | `absent` | `untested` |
| `residual.effect_clause.create-token` | `one_shot_effects` | 457 | 6 | 6 | `absent` | `untested` |
| `residual.effect_clause.life-change` | `one_shot_effects` | 397 | 6 | 6 | `absent` | `untested` |
| `residual.effect_clause.destroy-mass` | `one_shot_effects` | 137 | 6 | 6 | `absent` | `untested` |
| `residual.keyword_dependency.phasing` | `keyword_mechanics` | 12 | 6 | 6 | `absent` | `untested` |
| `residual.keyword_dependency.conspire` | `keyword_mechanics` | 11 | 6 | 6 | `absent` | `untested` |
| `residual.keyword_dependency.provoke` | `keyword_mechanics` | 8 | 6 | 6 | `absent` | `untested` |
| `residual.effect_clause.exile` | `one_shot_effects` | 482 | 5 | 5 | `absent` | `untested` |

## Boundary

Inventory and classification are not implementation or trust. Universal systems remain conservatively below snapshot-complete until all required rules, pieces, rulings, and interactions close.
