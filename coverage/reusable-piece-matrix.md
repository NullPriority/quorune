---
title: "Reusable rules piece matrix"
status: "generated"
authoritative_source: "coverage/reusable-piece-matrix.json.gz"
verified: "557743591427c99e1d5cf3dd5ba04d181af95689aeccd03f8ef87af11ec73c91"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Reusable rules piece matrix

Current Oracle IR material ability and residual spans plus all registered capabilities, mechanics, handlers, components, and pinned rule references. This inventories current source relations without claiming universal runtime completion.

Counts official ruling presence by Oracle ID. Ruling prose is not yet behaviorally classified, so these counts are composition evidence rather than coverage claims.

## Snapshot

- Profile: `commander_review`
- Ontology: `reusable-pieces-v1`
- Pieces: 5,107
- Cards indexed: 31,623
- Material abilities classified: 59,747
- Unclassified material spans: 0
- Mapped pinned rules: 1,810 / 3,309
- Applicable piece pairs: 203,879
- Covered piece pairs: 1,948

## Ontology classes

| Class | Pieces |
|---|---:|
| `actions_permissions` — Actions, permissions, and prohibitions | 478 |
| `card_forms` — Card types and specialized forms | 15 |
| `choices_continuations` — Modes, targets, choices, and continuations | 24 |
| `combat` — Combat | 53 |
| `compiler_cardprogram` — Compiler and CardProgram pieces | 2,765 |
| `continuous_effects` — Static abilities and continuous effects | 183 |
| `costs_mana` — Costs and mana | 22 |
| `events_mutations` — Typed events and mutations | 205 |
| `keyword_mechanics` — Keyword actions and keyword abilities | 841 |
| `multiplayer_commander` — Multiplayer, Commander, and profile pieces | 7 |
| `object_identity` — Object identity and lifetime | 105 |
| `one_shot_effects` — One-shot semantic effects | 291 |
| `players_format` — Players, relationships, and format state | 6 |
| `proposals` — Casting and activation proposals | 70 |
| `quantities` — Quantity and value expressions | 2 |
| `references` — References | 1 |
| `replacement_prevention` — Replacement and prevention | 36 |
| `triggers` — Triggers | 3 |

## Universal systems

| System | Status | Pieces | Blocking pieces |
|---|---|---:|---:|
| `action_legality_casting_activation_costs_mana` | `inventoried` | 570 | 6 |
| `combat` | `compositional` | 53 | 0 |
| `derived_characteristics_static_layers` | `inventoried` | 183 | 7 |
| `generic_triggers_stack_placement` | `inventoried` | 3 | 3 |
| `multiplayer_player_leaving_commander` | `compositional` | 13 | 0 |
| `objects_identity_zones_faces_copies` | `inventoried` | 120 | 3 |
| `replacement_prevention` | `inventoried` | 36 | 4 |
| `state_turn_loops_stabilization` | `inventoried` | 0 | 0 |
| `targets_modes_searches_references_choices` | `inventoried` | 27 | 11 |
| `typed_transactions_events_mutations` | `inventoried` | 496 | 90 |

## Highest current blocker leverage

| Piece | Class | Residuals | Sole blockers | Expected cards | Runtime | Assurance |
|---|---|---:|---:|---:|---|---|
| `residual.continuous_layer.continuous-effect-layers-and-dependencies` | `continuous_effects` | 2,070 | 850 | 850 | `absent` | `untested` |
| `residual.effect_clause.unparsed-clause-grammar` | `one_shot_effects` | 2,125 | 94 | 94 | `absent` | `untested` |
| `residual.activated_effect.unparsed-clause-grammar` | `one_shot_effects` | 781 | 61 | 61 | `absent` | `untested` |
| `residual.effect_clause.create-token` | `one_shot_effects` | 681 | 17 | 17 | `absent` | `untested` |
| `residual.effect_clause.return` | `one_shot_effects` | 740 | 15 | 15 | `absent` | `untested` |
| `residual.effect_clause.exile` | `one_shot_effects` | 898 | 10 | 10 | `absent` | `untested` |
| `residual.effect_clause.deal-damage` | `one_shot_effects` | 718 | 10 | 10 | `absent` | `untested` |
| `residual.effect_clause.put-onto-battlefield` | `one_shot_effects` | 371 | 9 | 9 | `absent` | `untested` |
| `residual.effect_clause.typed-spell-additional-cost-clause` | `one_shot_effects` | 69 | 8 | 8 | `absent` | `untested` |
| `residual.effect_clause.put-counter` | `one_shot_effects` | 615 | 7 | 7 | `absent` | `untested` |
| `residual.replacement.damage-prevention` | `replacement_prevention` | 105 | 7 | 7 | `absent` | `untested` |
| `residual.effect_clause.life-change` | `one_shot_effects` | 543 | 5 | 5 | `absent` | `untested` |
| `residual.mechanic_dependency.colored-hand-reveal-results` | `keyword_mechanics` | 5 | 5 | 5 | `absent` | `untested` |
| `residual.effect_clause.look-reveal` | `one_shot_effects` | 482 | 4 | 4 | `absent` | `untested` |
| `residual.effect_clause.sacrifice` | `one_shot_effects` | 335 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.exile` | `one_shot_effects` | 213 | 4 | 4 | `absent` | `untested` |
| `residual.effect_clause.destroy-mass` | `one_shot_effects` | 122 | 4 | 4 | `absent` | `untested` |
| `residual.effect_clause.copy` | `one_shot_effects` | 78 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.destroy-target` | `one_shot_effects` | 37 | 4 | 4 | `absent` | `untested` |
| `residual.activated_effect.deal-damage` | `one_shot_effects` | 178 | 3 | 3 | `absent` | `untested` |
| `residual.activated_effect.return` | `one_shot_effects` | 167 | 3 | 3 | `absent` | `untested` |
| `residual.activated_effect.create-token` | `one_shot_effects` | 157 | 3 | 3 | `absent` | `untested` |
| `residual.activated_effect.draw` | `one_shot_effects` | 144 | 3 | 3 | `absent` | `untested` |
| `residual.effect_clause.destroy` | `one_shot_effects` | 107 | 3 | 3 | `absent` | `untested` |
| `residual.activated_effect.copy` | `one_shot_effects` | 47 | 3 | 3 | `absent` | `untested` |
| `residual.activated_effect.gain-control` | `one_shot_effects` | 25 | 3 | 3 | `absent` | `untested` |
| `residual.effect_clause.variable-nonmana-repeated-granted-instances-and-permanent-spells` | `one_shot_effects` | 5 | 3 | 3 | `absent` | `untested` |
| `residual.activated_effect.put-counter` | `one_shot_effects` | 198 | 2 | 2 | `absent` | `untested` |
| `residual.activated_effect.put-onto-battlefield` | `one_shot_effects` | 141 | 2 | 2 | `absent` | `untested` |
| `residual.effect_clause.counter` | `one_shot_effects` | 97 | 2 | 2 | `absent` | `untested` |

## Boundary

Inventory and classification are not implementation or trust. Universal systems remain conservatively below snapshot-complete until all required rules, pieces, rulings, and interactions close.
