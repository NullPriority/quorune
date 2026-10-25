---
title: "Reusable rules piece matrix"
status: "generated"
authoritative_source: "coverage/reusable-piece-matrix.json.gz"
verified: "7f614255a6e97002ad475c85c7d6dabba65ccc6d9571961003442cd0fdde554a"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Reusable rules piece matrix

Current Oracle IR material ability and residual spans plus all registered capabilities, mechanics, handlers, components, and pinned rule references. This inventories current source relations without claiming universal runtime completion.

Counts official ruling presence by Oracle ID. Ruling prose is not yet behaviorally classified, so these counts are composition evidence rather than coverage claims.

## Snapshot

- Profile: `commander_review`
- Ontology: `reusable-pieces-v1`
- Pieces: 3,807
- Cards indexed: 31,623
- Material abilities classified: 59,885
- Unclassified material spans: 0
- Mapped pinned rules: 1,487 / 3,309
- Applicable piece pairs: 139,985
- Covered piece pairs: 1,782

## Ontology classes

| Class | Pieces |
|---|---:|
| `actions_permissions` — Actions, permissions, and prohibitions | 256 |
| `card_forms` — Card types and specialized forms | 11 |
| `choices_continuations` — Modes, targets, choices, and continuations | 20 |
| `combat` — Combat | 43 |
| `compiler_cardprogram` — Compiler and CardProgram pieces | 2,111 |
| `continuous_effects` — Static abilities and continuous effects | 116 |
| `costs_mana` — Costs and mana | 15 |
| `events_mutations` — Typed events and mutations | 153 |
| `keyword_mechanics` — Keyword actions and keyword abilities | 684 |
| `multiplayer_commander` — Multiplayer, Commander, and profile pieces | 6 |
| `object_identity` — Object identity and lifetime | 60 |
| `one_shot_effects` — One-shot semantic effects | 241 |
| `players_format` — Players, relationships, and format state | 3 |
| `proposals` — Casting and activation proposals | 49 |
| `quantities` — Quantity and value expressions | 2 |
| `references` — References | 1 |
| `replacement_prevention` — Replacement and prevention | 33 |
| `triggers` — Triggers | 3 |

## Universal systems

| System | Status | Pieces | Blocking pieces |
|---|---|---:|---:|
| `action_legality_casting_activation_costs_mana` | `inventoried` | 320 | 6 |
| `combat` | `compositional` | 43 | 0 |
| `derived_characteristics_static_layers` | `inventoried` | 116 | 7 |
| `generic_triggers_stack_placement` | `inventoried` | 3 | 3 |
| `multiplayer_player_leaving_commander` | `compositional` | 9 | 0 |
| `objects_identity_zones_faces_copies` | `inventoried` | 71 | 2 |
| `replacement_prevention` | `inventoried` | 33 | 4 |
| `state_turn_loops_stabilization` | `inventoried` | 0 | 0 |
| `targets_modes_searches_references_choices` | `inventoried` | 23 | 11 |
| `typed_transactions_events_mutations` | `inventoried` | 394 | 90 |

## Highest current blocker leverage

| Piece | Class | Residuals | Sole blockers | Expected cards | Runtime | Assurance |
|---|---|---:|---:|---:|---|---|
| `residual.continuous_layer.continuous-effect-layers-and-dependencies` | `continuous_effects` | 2,559 | 1,040 | 1,040 | `absent` | `untested` |
| `residual.activated_effect.unparsed-clause-grammar` | `one_shot_effects` | 1,087 | 104 | 104 | `absent` | `untested` |
| `residual.effect_clause.unparsed-clause-grammar` | `one_shot_effects` | 1,563 | 59 | 59 | `absent` | `untested` |
| `residual.keyword_dependency.cipher` | `keyword_mechanics` | 15 | 11 | 11 | `absent` | `untested` |
| `residual.effect_clause.typed-spell-additional-cost-clause` | `one_shot_effects` | 72 | 10 | 10 | `absent` | `untested` |
| `residual.activated_effect.create-token` | `one_shot_effects` | 217 | 9 | 9 | `absent` | `untested` |
| `residual.keyword_dependency.assist` | `keyword_mechanics` | 16 | 9 | 9 | `absent` | `untested` |
| `residual.effect_clause.return` | `one_shot_effects` | 406 | 6 | 6 | `absent` | `untested` |
| `residual.effect_clause.life-change` | `one_shot_effects` | 350 | 6 | 6 | `absent` | `untested` |
| `residual.activated_effect.life-change` | `one_shot_effects` | 148 | 6 | 6 | `absent` | `untested` |
| `residual.effect_clause.destroy-mass` | `one_shot_effects` | 137 | 6 | 6 | `absent` | `untested` |
| `residual.keyword_dependency.phasing` | `keyword_mechanics` | 12 | 6 | 6 | `absent` | `untested` |
| `residual.activated_effect.draw` | `one_shot_effects` | 211 | 5 | 5 | `absent` | `untested` |
| `residual.replacement.damage-prevention` | `replacement_prevention` | 114 | 5 | 5 | `absent` | `untested` |
| `residual.keyword_dependency.equip` | `keyword_mechanics` | 8 | 5 | 5 | `absent` | `untested` |
| `residual.effect_clause.exile` | `one_shot_effects` | 460 | 4 | 4 | `absent` | `untested` |
| `residual.effect_clause.create-token` | `one_shot_effects` | 406 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.exile` | `one_shot_effects` | 248 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.put-onto-battlefield` | `one_shot_effects` | 161 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.destroy-target` | `one_shot_effects` | 52 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.return` | `one_shot_effects` | 196 | 3 | 3 | `absent` | `untested` |
| `residual.activated_effect.copy` | `one_shot_effects` | 63 | 3 | 3 | `absent` | `untested` |
| `residual.mechanic_dependency.closed-effect-program` | `keyword_mechanics` | 43 | 3 | 3 | `absent` | `untested` |
| `residual.mechanic_dependency.tap-and-untap` | `keyword_mechanics` | 16 | 3 | 3 | `absent` | `untested` |
| `residual.mechanic_dependency.cr-111-tokens` | `keyword_mechanics` | 15 | 3 | 3 | `absent` | `untested` |
| `residual.mechanic_dependency.fixed-typed-event-effect-trigger` | `keyword_mechanics` | 13 | 3 | 3 | `absent` | `untested` |
| `residual.keyword_dependency.paradigm` | `keyword_mechanics` | 5 | 3 | 3 | `absent` | `untested` |
| `residual.target_or_choice.typed-enchant-restriction` | `choices_continuations` | 3 | 3 | 3 | `absent` | `untested` |
| `residual.effect_clause.ordered-effect-composition` | `one_shot_effects` | 2,818 | 2 | 2 | `absent` | `untested` |
| `residual.effect_clause.draw` | `one_shot_effects` | 292 | 2 | 2 | `absent` | `untested` |

## Boundary

Inventory and classification are not implementation or trust. Universal systems remain conservatively below snapshot-complete until all required rules, pieces, rulings, and interactions close.
