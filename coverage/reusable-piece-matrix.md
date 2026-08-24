---
title: "Reusable rules piece matrix"
status: "generated"
authoritative_source: "coverage/reusable-piece-matrix.json.gz"
verified: "53d3b8596fbb9662f0cb18ac75714ea29d60702a1cd88acd8121b4f5e64813a7"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Reusable rules piece matrix

Current Oracle IR material ability and residual spans plus all registered capabilities, mechanics, handlers, components, and pinned rule references. This inventories current source relations without claiming universal runtime completion.

Counts official ruling presence by Oracle ID. Ruling prose is not yet behaviorally classified, so these counts are composition evidence rather than coverage claims.

## Snapshot

- Profile: `commander_review`
- Ontology: `reusable-pieces-v1`
- Pieces: 1,872
- Cards indexed: 31,623
- Material abilities classified: 59,378
- Unclassified material spans: 0
- Mapped pinned rules: 918 / 3,309
- Applicable piece pairs: 48,741
- Covered piece pairs: 741

## Ontology classes

| Class | Pieces |
|---|---:|
| `actions_permissions` — Actions, permissions, and prohibitions | 65 |
| `card_forms` — Card types and specialized forms | 5 |
| `choices_continuations` — Modes, targets, choices, and continuations | 13 |
| `combat` — Combat | 24 |
| `compiler_cardprogram` — Compiler and CardProgram pieces | 829 |
| `continuous_effects` — Static abilities and continuous effects | 39 |
| `costs_mana` — Costs and mana | 8 |
| `events_mutations` — Typed events and mutations | 109 |
| `keyword_mechanics` — Keyword actions and keyword abilities | 536 |
| `multiplayer_commander` — Multiplayer, Commander, and profile pieces | 4 |
| `object_identity` — Object identity and lifetime | 30 |
| `one_shot_effects` — One-shot semantic effects | 163 |
| `players_format` — Players, relationships, and format state | 2 |
| `proposals` — Casting and activation proposals | 20 |
| `quantities` — Quantity and value expressions | 1 |
| `references` — References | 1 |
| `replacement_prevention` — Replacement and prevention | 21 |
| `triggers` — Triggers | 2 |

## Universal systems

| System | Status | Pieces | Blocking pieces |
|---|---|---:|---:|
| `action_legality_casting_activation_costs_mana` | `inventoried` | 93 | 6 |
| `combat` | `compositional` | 24 | 0 |
| `derived_characteristics_static_layers` | `inventoried` | 39 | 7 |
| `generic_triggers_stack_placement` | `inventoried` | 2 | 2 |
| `multiplayer_player_leaving_commander` | `compositional` | 6 | 0 |
| `objects_identity_zones_faces_copies` | `compositional` | 35 | 0 |
| `replacement_prevention` | `inventoried` | 21 | 4 |
| `state_turn_loops_stabilization` | `inventoried` | 0 | 0 |
| `targets_modes_searches_references_choices` | `inventoried` | 15 | 10 |
| `typed_transactions_events_mutations` | `inventoried` | 272 | 77 |

## Highest current blocker leverage

| Piece | Class | Residuals | Sole blockers | Expected cards | Runtime | Assurance |
|---|---|---:|---:|---:|---|---|
| `residual.continuous_layer.continuous-effect-layers-and-dependencies` | `continuous_effects` | 8,334 | 3,800 | 3,800 | `absent` | `untested` |
| `residual.effect_clause.unparsed-clause-grammar` | `one_shot_effects` | 2,574 | 239 | 239 | `absent` | `untested` |
| `residual.activated_effect.unparsed-clause-grammar` | `one_shot_effects` | 2,021 | 146 | 146 | `absent` | `untested` |
| `residual.activated_effect.create-token` | `one_shot_effects` | 319 | 22 | 22 | `absent` | `untested` |
| `residual.replacement.damage-prevention` | `replacement_prevention` | 168 | 21 | 21 | `absent` | `untested` |
| `residual.effect_clause.create-token` | `one_shot_effects` | 565 | 17 | 17 | `absent` | `untested` |
| `residual.effect_clause.typed-spell-additional-cost-clause` | `one_shot_effects` | 106 | 17 | 17 | `absent` | `untested` |
| `residual.effect_clause.life-change` | `one_shot_effects` | 511 | 16 | 16 | `absent` | `untested` |
| `residual.activated_effect.put-onto-battlefield` | `one_shot_effects` | 221 | 14 | 14 | `absent` | `untested` |
| `residual.effect_clause.exile` | `one_shot_effects` | 592 | 12 | 12 | `absent` | `untested` |
| `residual.effect_clause.return` | `one_shot_effects` | 611 | 11 | 11 | `absent` | `untested` |
| `residual.effect_clause.sacrifice` | `one_shot_effects` | 110 | 11 | 11 | `absent` | `untested` |
| `residual.keyword_dependency.rebound` | `keyword_mechanics` | 34 | 10 | 10 | `absent` | `untested` |
| `residual.keyword_dependency.banding` | `keyword_mechanics` | 13 | 10 | 10 | `absent` | `untested` |
| `residual.keyword_dependency.retrace` | `keyword_mechanics` | 17 | 9 | 9 | `absent` | `untested` |
| `residual.activated_effect.life-change` | `one_shot_effects` | 204 | 8 | 8 | `absent` | `untested` |
| `residual.effect_clause.add-mana` | `one_shot_effects` | 57 | 8 | 8 | `absent` | `untested` |
| `residual.keyword_dependency.start-your-engines` | `keyword_mechanics` | 40 | 8 | 8 | `absent` | `untested` |
| `residual.keyword_dependency.living-weapon` | `keyword_mechanics` | 19 | 8 | 8 | `absent` | `untested` |
| `residual.keyword_dependency.extort` | `keyword_mechanics` | 18 | 8 | 8 | `absent` | `untested` |
| `residual.keyword_dependency.umbra-armor` | `keyword_mechanics` | 15 | 8 | 8 | `absent` | `untested` |
| `residual.keyword_dependency.enlist` | `keyword_mechanics` | 12 | 8 | 8 | `absent` | `untested` |
| `residual.keyword_dependency.equip` | `keyword_mechanics` | 25 | 7 | 7 | `absent` | `untested` |
| `residual.keyword_dependency.myriad` | `keyword_mechanics` | 23 | 7 | 7 | `absent` | `untested` |
| `residual.effect_clause.counter` | `one_shot_effects` | 231 | 6 | 6 | `absent` | `untested` |
| `residual.effect_clause.destroy-mass` | `one_shot_effects` | 149 | 6 | 6 | `absent` | `untested` |
| `residual.activated_effect.destroy-target` | `one_shot_effects` | 117 | 6 | 6 | `absent` | `untested` |
| `residual.keyword_dependency.split-second` | `keyword_mechanics` | 21 | 6 | 6 | `absent` | `untested` |
| `residual.keyword_dependency.assist` | `keyword_mechanics` | 16 | 6 | 6 | `absent` | `untested` |
| `residual.keyword_dependency.cipher` | `keyword_mechanics` | 15 | 6 | 6 | `absent` | `untested` |

## Boundary

Inventory and classification are not implementation or trust. Universal systems remain conservatively below snapshot-complete until all required rules, pieces, rulings, and interactions close.
