---
title: "Reusable rules piece matrix"
status: "generated"
authoritative_source: "coverage/reusable-piece-matrix.json.gz"
verified: "bd0088ec86f040ab8ad66349ecaa4ae6537b0770c9d318e61e18d5ef8e11b973"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Reusable rules piece matrix

Current Oracle IR material ability and residual spans plus all registered capabilities, mechanics, handlers, components, and pinned rule references. This inventories current source relations without claiming universal runtime completion.

Counts official ruling presence by Oracle ID. Ruling prose is not yet behaviorally classified, so these counts are composition evidence rather than coverage claims.

## Snapshot

- Profile: `commander_review`
- Ontology: `reusable-pieces-v1`
- Pieces: 3,495
- Cards indexed: 31,623
- Material abilities classified: 59,955
- Unclassified material spans: 0
- Mapped pinned rules: 1,389 / 3,309
- Applicable piece pairs: 129,972
- Covered piece pairs: 1,547

## Ontology classes

| Class | Pieces |
|---|---:|
| `actions_permissions` — Actions, permissions, and prohibitions | 192 |
| `card_forms` — Card types and specialized forms | 9 |
| `choices_continuations` — Modes, targets, choices, and continuations | 16 |
| `combat` — Combat | 38 |
| `compiler_cardprogram` — Compiler and CardProgram pieces | 1,971 |
| `continuous_effects` — Static abilities and continuous effects | 101 |
| `costs_mana` — Costs and mana | 13 |
| `events_mutations` — Typed events and mutations | 140 |
| `keyword_mechanics` — Keyword actions and keyword abilities | 652 |
| `multiplayer_commander` — Multiplayer, Commander, and profile pieces | 5 |
| `object_identity` — Object identity and lifetime | 56 |
| `one_shot_effects` — One-shot semantic effects | 219 |
| `players_format` — Players, relationships, and format state | 3 |
| `proposals` — Casting and activation proposals | 42 |
| `quantities` — Quantity and value expressions | 2 |
| `references` — References | 1 |
| `replacement_prevention` — Replacement and prevention | 32 |
| `triggers` — Triggers | 3 |

## Universal systems

| System | Status | Pieces | Blocking pieces |
|---|---|---:|---:|
| `action_legality_casting_activation_costs_mana` | `inventoried` | 247 | 6 |
| `combat` | `compositional` | 38 | 0 |
| `derived_characteristics_static_layers` | `inventoried` | 101 | 7 |
| `generic_triggers_stack_placement` | `inventoried` | 3 | 3 |
| `multiplayer_player_leaving_commander` | `compositional` | 8 | 0 |
| `objects_identity_zones_faces_copies` | `inventoried` | 65 | 2 |
| `replacement_prevention` | `inventoried` | 32 | 4 |
| `state_turn_loops_stabilization` | `inventoried` | 0 | 0 |
| `targets_modes_searches_references_choices` | `inventoried` | 19 | 11 |
| `typed_transactions_events_mutations` | `inventoried` | 359 | 90 |

## Highest current blocker leverage

| Piece | Class | Residuals | Sole blockers | Expected cards | Runtime | Assurance |
|---|---|---:|---:|---:|---|---|
| `residual.continuous_layer.continuous-effect-layers-and-dependencies` | `continuous_effects` | 2,892 | 1,269 | 1,269 | `absent` | `untested` |
| `residual.activated_effect.unparsed-clause-grammar` | `one_shot_effects` | 1,157 | 110 | 110 | `absent` | `untested` |
| `residual.effect_clause.unparsed-clause-grammar` | `one_shot_effects` | 1,668 | 58 | 58 | `absent` | `untested` |
| `residual.keyword_dependency.banding` | `keyword_mechanics` | 24 | 19 | 19 | `absent` | `untested` |
| `residual.effect_clause.typed-spell-additional-cost-clause` | `one_shot_effects` | 97 | 16 | 16 | `absent` | `untested` |
| `residual.replacement.damage-prevention` | `replacement_prevention` | 123 | 14 | 14 | `absent` | `untested` |
| `residual.keyword_dependency.cipher` | `keyword_mechanics` | 15 | 11 | 11 | `absent` | `untested` |
| `residual.activated_effect.create-token` | `one_shot_effects` | 233 | 10 | 10 | `absent` | `untested` |
| `residual.keyword_dependency.assist` | `keyword_mechanics` | 16 | 9 | 9 | `absent` | `untested` |
| `residual.effect_clause.return` | `one_shot_effects` | 431 | 8 | 8 | `absent` | `untested` |
| `residual.activated_effect.exile` | `one_shot_effects` | 260 | 8 | 8 | `absent` | `untested` |
| `residual.keyword_dependency.fuse` | `keyword_mechanics` | 34 | 8 | 8 | `absent` | `untested` |
| `residual.effect_clause.create-token` | `one_shot_effects` | 424 | 6 | 6 | `absent` | `untested` |
| `residual.effect_clause.life-change` | `one_shot_effects` | 358 | 6 | 6 | `absent` | `untested` |
| `residual.effect_clause.counter` | `one_shot_effects` | 154 | 6 | 6 | `absent` | `untested` |
| `residual.activated_effect.life-change` | `one_shot_effects` | 151 | 6 | 6 | `absent` | `untested` |
| `residual.effect_clause.destroy-mass` | `one_shot_effects` | 137 | 6 | 6 | `absent` | `untested` |
| `residual.keyword_dependency.phasing` | `keyword_mechanics` | 12 | 6 | 6 | `absent` | `untested` |
| `residual.keyword_dependency.provoke` | `keyword_mechanics` | 8 | 6 | 6 | `absent` | `untested` |
| `residual.target_or_choice.typed-enchant-restriction` | `choices_continuations` | 7 | 6 | 6 | `absent` | `untested` |
| `residual.effect_clause.exile` | `one_shot_effects` | 477 | 5 | 5 | `absent` | `untested` |
| `residual.activated_effect.destroy-target` | `one_shot_effects` | 54 | 5 | 5 | `absent` | `untested` |
| `residual.keyword_dependency.increment` | `keyword_mechanics` | 9 | 5 | 5 | `absent` | `untested` |
| `residual.effect_clause.destroy-target` | `one_shot_effects` | 268 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.draw` | `one_shot_effects` | 221 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.return` | `one_shot_effects` | 203 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.put-onto-battlefield` | `one_shot_effects` | 162 | 4 | 4 | `absent` | `untested` |
| `residual.keyword_dependency.equip` | `keyword_mechanics` | 8 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.copy` | `one_shot_effects` | 63 | 3 | 3 | `absent` | `untested` |
| `residual.mechanic_dependency.closed-effect-program` | `keyword_mechanics` | 42 | 3 | 3 | `absent` | `untested` |

## Boundary

Inventory and classification are not implementation or trust. Universal systems remain conservatively below snapshot-complete until all required rules, pieces, rulings, and interactions close.
