---
title: "Reusable rules piece matrix"
status: "generated"
authoritative_source: "coverage/reusable-piece-matrix.json.gz"
verified: "984b041f2f16c99a215380998bb693e57e1c0fce08c1b986aecca3776b9de901"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Reusable rules piece matrix

Current Oracle IR material ability and residual spans plus all registered capabilities, mechanics, handlers, components, and pinned rule references. This inventories current source relations without claiming universal runtime completion.

Counts official ruling presence by Oracle ID. Ruling prose is not yet behaviorally classified, so these counts are composition evidence rather than coverage claims.

## Snapshot

- Profile: `commander_review`
- Ontology: `reusable-pieces-v1`
- Pieces: 3,937
- Cards indexed: 31,623
- Material abilities classified: 59,960
- Unclassified material spans: 0
- Mapped pinned rules: 1,541 / 3,309
- Applicable piece pairs: 148,174
- Covered piece pairs: 1,806

## Ontology classes

| Class | Pieces |
|---|---:|
| `actions_permissions` — Actions, permissions, and prohibitions | 292 |
| `card_forms` — Card types and specialized forms | 12 |
| `choices_continuations` — Modes, targets, choices, and continuations | 20 |
| `combat` — Combat | 47 |
| `compiler_cardprogram` — Compiler and CardProgram pieces | 2,149 |
| `continuous_effects` — Static abilities and continuous effects | 144 |
| `costs_mana` — Costs and mana | 15 |
| `events_mutations` — Typed events and mutations | 156 |
| `keyword_mechanics` — Keyword actions and keyword abilities | 691 |
| `multiplayer_commander` — Multiplayer, Commander, and profile pieces | 6 |
| `object_identity` — Object identity and lifetime | 63 |
| `one_shot_effects` — One-shot semantic effects | 245 |
| `players_format` — Players, relationships, and format state | 3 |
| `proposals` — Casting and activation proposals | 55 |
| `quantities` — Quantity and value expressions | 2 |
| `references` — References | 1 |
| `replacement_prevention` — Replacement and prevention | 33 |
| `triggers` — Triggers | 3 |

## Universal systems

| System | Status | Pieces | Blocking pieces |
|---|---|---:|---:|
| `action_legality_casting_activation_costs_mana` | `inventoried` | 362 | 6 |
| `combat` | `compositional` | 47 | 0 |
| `derived_characteristics_static_layers` | `inventoried` | 144 | 7 |
| `generic_triggers_stack_placement` | `inventoried` | 3 | 3 |
| `multiplayer_player_leaving_commander` | `compositional` | 9 | 0 |
| `objects_identity_zones_faces_copies` | `inventoried` | 75 | 3 |
| `replacement_prevention` | `inventoried` | 33 | 4 |
| `state_turn_loops_stabilization` | `inventoried` | 0 | 0 |
| `targets_modes_searches_references_choices` | `inventoried` | 23 | 11 |
| `typed_transactions_events_mutations` | `inventoried` | 401 | 90 |

## Highest current blocker leverage

| Piece | Class | Residuals | Sole blockers | Expected cards | Runtime | Assurance |
|---|---|---:|---:|---:|---|---|
| `residual.continuous_layer.continuous-effect-layers-and-dependencies` | `continuous_effects` | 2,410 | 954 | 954 | `absent` | `untested` |
| `residual.activated_effect.unparsed-clause-grammar` | `one_shot_effects` | 1,071 | 102 | 102 | `absent` | `untested` |
| `residual.effect_clause.unparsed-clause-grammar` | `one_shot_effects` | 1,529 | 56 | 56 | `absent` | `untested` |
| `residual.effect_clause.typed-spell-additional-cost-clause` | `one_shot_effects` | 72 | 11 | 11 | `absent` | `untested` |
| `residual.activated_effect.create-token` | `one_shot_effects` | 211 | 10 | 10 | `absent` | `untested` |
| `residual.keyword_dependency.assist` | `keyword_mechanics` | 16 | 9 | 9 | `absent` | `untested` |
| `residual.effect_clause.return` | `one_shot_effects` | 404 | 6 | 6 | `absent` | `untested` |
| `residual.effect_clause.life-change` | `one_shot_effects` | 347 | 6 | 6 | `absent` | `untested` |
| `residual.activated_effect.life-change` | `one_shot_effects` | 144 | 6 | 6 | `absent` | `untested` |
| `residual.effect_clause.destroy-mass` | `one_shot_effects` | 137 | 6 | 6 | `absent` | `untested` |
| `residual.keyword_dependency.phasing` | `keyword_mechanics` | 12 | 6 | 6 | `absent` | `untested` |
| `residual.activated_effect.draw` | `one_shot_effects` | 198 | 5 | 5 | `absent` | `untested` |
| `residual.replacement.damage-prevention` | `replacement_prevention` | 113 | 5 | 5 | `absent` | `untested` |
| `residual.keyword_dependency.equip` | `keyword_mechanics` | 8 | 5 | 5 | `absent` | `untested` |
| `residual.effect_clause.exile` | `one_shot_effects` | 460 | 4 | 4 | `absent` | `untested` |
| `residual.effect_clause.create-token` | `one_shot_effects` | 404 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.exile` | `one_shot_effects` | 247 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.put-onto-battlefield` | `one_shot_effects` | 161 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.destroy-target` | `one_shot_effects` | 52 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.return` | `one_shot_effects` | 192 | 3 | 3 | `absent` | `untested` |
| `residual.activated_effect.copy` | `one_shot_effects` | 61 | 3 | 3 | `absent` | `untested` |
| `residual.mechanic_dependency.closed-effect-program` | `keyword_mechanics` | 20 | 3 | 3 | `absent` | `untested` |
| `residual.mechanic_dependency.tap-and-untap` | `keyword_mechanics` | 16 | 3 | 3 | `absent` | `untested` |
| `residual.mechanic_dependency.cr-111-tokens` | `keyword_mechanics` | 7 | 3 | 3 | `absent` | `untested` |
| `residual.keyword_dependency.paradigm` | `keyword_mechanics` | 5 | 3 | 3 | `absent` | `untested` |
| `residual.target_or_choice.typed-enchant-restriction` | `choices_continuations` | 3 | 3 | 3 | `absent` | `untested` |
| `residual.effect_clause.ordered-effect-composition` | `one_shot_effects` | 2,758 | 2 | 2 | `absent` | `untested` |
| `residual.effect_clause.draw` | `one_shot_effects` | 281 | 2 | 2 | `absent` | `untested` |
| `residual.effect_clause.look-reveal` | `one_shot_effects` | 267 | 2 | 2 | `absent` | `untested` |
| `residual.activated_effect.deal-damage` | `one_shot_effects` | 228 | 2 | 2 | `absent` | `untested` |

## Boundary

Inventory and classification are not implementation or trust. Universal systems remain conservatively below snapshot-complete until all required rules, pieces, rulings, and interactions close.
