---
title: "Reusable rules piece matrix"
status: "generated"
authoritative_source: "coverage/reusable-piece-matrix.json.gz"
verified: "ad8172d2bf606fb498f88da82d0f6348e1145e8785e7045516790429b11ad5d8"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Reusable rules piece matrix

Current Oracle IR material ability and residual spans plus all registered capabilities, mechanics, handlers, components, and pinned rule references. This inventories current source relations without claiming universal runtime completion.

Counts official ruling presence by Oracle ID. Ruling prose is not yet behaviorally classified, so these counts are composition evidence rather than coverage claims.

## Snapshot

- Profile: `commander_review`
- Ontology: `reusable-pieces-v1`
- Pieces: 2,696
- Cards indexed: 31,623
- Material abilities classified: 59,356
- Unclassified material spans: 0
- Mapped pinned rules: 1,048 / 3,309
- Applicable piece pairs: 84,707
- Covered piece pairs: 988

## Ontology classes

| Class | Pieces |
|---|---:|
| `actions_permissions` — Actions, permissions, and prohibitions | 105 |
| `card_forms` — Card types and specialized forms | 8 |
| `choices_continuations` — Modes, targets, choices, and continuations | 14 |
| `combat` — Combat | 25 |
| `compiler_cardprogram` — Compiler and CardProgram pieces | 1,479 |
| `continuous_effects` — Static abilities and continuous effects | 53 |
| `costs_mana` — Costs and mana | 9 |
| `events_mutations` — Typed events and mutations | 117 |
| `keyword_mechanics` — Keyword actions and keyword abilities | 593 |
| `multiplayer_commander` — Multiplayer, Commander, and profile pieces | 5 |
| `object_identity` — Object identity and lifetime | 36 |
| `one_shot_effects` — One-shot semantic effects | 191 |
| `players_format` — Players, relationships, and format state | 2 |
| `proposals` — Casting and activation proposals | 31 |
| `quantities` — Quantity and value expressions | 1 |
| `references` — References | 1 |
| `replacement_prevention` — Replacement and prevention | 23 |
| `triggers` — Triggers | 3 |

## Universal systems

| System | Status | Pieces | Blocking pieces |
|---|---|---:|---:|
| `action_legality_casting_activation_costs_mana` | `inventoried` | 145 | 6 |
| `combat` | `compositional` | 25 | 0 |
| `derived_characteristics_static_layers` | `inventoried` | 53 | 7 |
| `generic_triggers_stack_placement` | `inventoried` | 3 | 3 |
| `multiplayer_player_leaving_commander` | `compositional` | 7 | 0 |
| `objects_identity_zones_faces_copies` | `inventoried` | 44 | 1 |
| `replacement_prevention` | `inventoried` | 23 | 4 |
| `state_turn_loops_stabilization` | `inventoried` | 0 | 0 |
| `targets_modes_searches_references_choices` | `inventoried` | 16 | 10 |
| `typed_transactions_events_mutations` | `inventoried` | 308 | 84 |

## Highest current blocker leverage

| Piece | Class | Residuals | Sole blockers | Expected cards | Runtime | Assurance |
|---|---|---:|---:|---:|---|---|
| `residual.continuous_layer.continuous-effect-layers-and-dependencies` | `continuous_effects` | 4,316 | 1,923 | 1,923 | `absent` | `untested` |
| `residual.effect_clause.unparsed-clause-grammar` | `one_shot_effects` | 2,045 | 170 | 170 | `absent` | `untested` |
| `residual.activated_effect.unparsed-clause-grammar` | `one_shot_effects` | 1,643 | 132 | 132 | `absent` | `untested` |
| `residual.keyword_dependency.banding` | `keyword_mechanics` | 24 | 19 | 19 | `absent` | `untested` |
| `residual.effect_clause.typed-spell-additional-cost-clause` | `one_shot_effects` | 106 | 18 | 18 | `absent` | `untested` |
| `residual.activated_effect.put-onto-battlefield` | `one_shot_effects` | 209 | 17 | 17 | `absent` | `untested` |
| `residual.effect_clause.life-change` | `one_shot_effects` | 468 | 16 | 16 | `absent` | `untested` |
| `residual.keyword_dependency.start-your-engines` | `keyword_mechanics` | 40 | 16 | 16 | `absent` | `untested` |
| `residual.replacement.damage-prevention` | `replacement_prevention` | 140 | 15 | 15 | `absent` | `untested` |
| `residual.effect_clause.create-token` | `one_shot_effects` | 538 | 13 | 13 | `absent` | `untested` |
| `residual.activated_effect.create-token` | `one_shot_effects` | 283 | 13 | 13 | `absent` | `untested` |
| `residual.keyword_dependency.myriad` | `keyword_mechanics` | 23 | 12 | 12 | `absent` | `untested` |
| `residual.mechanic_dependency.fading-remaining-lifecycle` | `keyword_mechanics` | 17 | 11 | 11 | `absent` | `untested` |
| `residual.effect_clause.return` | `one_shot_effects` | 484 | 10 | 10 | `absent` | `untested` |
| `residual.keyword_dependency.umbra-armor` | `keyword_mechanics` | 15 | 10 | 10 | `absent` | `untested` |
| `residual.activated_effect.life-change` | `one_shot_effects` | 180 | 9 | 9 | `absent` | `untested` |
| `residual.keyword_dependency.extort` | `keyword_mechanics` | 18 | 9 | 9 | `absent` | `untested` |
| `residual.keyword_dependency.assist` | `keyword_mechanics` | 16 | 9 | 9 | `absent` | `untested` |
| `residual.keyword_dependency.enlist` | `keyword_mechanics` | 12 | 9 | 9 | `absent` | `untested` |
| `residual.activated_effect.exile` | `one_shot_effects` | 291 | 8 | 8 | `absent` | `untested` |
| `residual.effect_clause.add-mana` | `one_shot_effects` | 57 | 8 | 8 | `absent` | `untested` |
| `residual.keyword_dependency.split-second` | `keyword_mechanics` | 21 | 8 | 8 | `absent` | `untested` |
| `residual.keyword_dependency.learn` | `keyword_mechanics` | 13 | 8 | 8 | `absent` | `untested` |
| `residual.effect_clause.exile` | `one_shot_effects` | 525 | 7 | 7 | `absent` | `untested` |
| `residual.effect_clause.destroy-mass` | `one_shot_effects` | 141 | 7 | 7 | `absent` | `untested` |
| `residual.mechanic_dependency.vanishing-remaining-lifecycle` | `keyword_mechanics` | 20 | 7 | 7 | `absent` | `untested` |
| `residual.keyword_dependency.cipher` | `keyword_mechanics` | 15 | 7 | 7 | `absent` | `untested` |
| `residual.mechanic_dependency.morph-unsupported-cost` | `keyword_mechanics` | 14 | 7 | 7 | `absent` | `untested` |
| `residual.effect_clause.look-reveal` | `one_shot_effects` | 303 | 6 | 6 | `absent` | `untested` |
| `residual.effect_clause.tap-state` | `one_shot_effects` | 298 | 6 | 6 | `absent` | `untested` |

## Boundary

Inventory and classification are not implementation or trust. Universal systems remain conservatively below snapshot-complete until all required rules, pieces, rulings, and interactions close.
