---
title: "Reusable rules piece matrix"
status: "generated"
authoritative_source: "coverage/reusable-piece-matrix.json.gz"
verified: "8ee7d45f7510d077931900665c50a1cec95db80aab77dd548fe26c28ca80fbb3"
audience: "compiler and rules contributors"
maintenance: "generated"
---

# Reusable rules piece matrix

Current Oracle IR material ability and residual spans plus all registered capabilities, mechanics, handlers, components, and pinned rule references. This inventories current source relations without claiming universal runtime completion.

Counts official ruling presence by Oracle ID. Ruling prose is not yet behaviorally classified, so these counts are composition evidence rather than coverage claims.

## Snapshot

- Profile: `commander_review`
- Ontology: `reusable-pieces-v1`
- Pieces: 2,612
- Cards indexed: 31,623
- Material abilities classified: 59,393
- Unclassified material spans: 0
- Mapped pinned rules: 1,024 / 3,309
- Applicable piece pairs: 80,229
- Covered piece pairs: 983

## Ontology classes

| Class | Pieces |
|---|---:|
| `actions_permissions` — Actions, permissions, and prohibitions | 89 |
| `card_forms` — Card types and specialized forms | 8 |
| `choices_continuations` — Modes, targets, choices, and continuations | 14 |
| `combat` — Combat | 24 |
| `compiler_cardprogram` — Compiler and CardProgram pieces | 1,424 |
| `continuous_effects` — Static abilities and continuous effects | 52 |
| `costs_mana` — Costs and mana | 9 |
| `events_mutations` — Typed events and mutations | 116 |
| `keyword_mechanics` — Keyword actions and keyword abilities | 591 |
| `multiplayer_commander` — Multiplayer, Commander, and profile pieces | 5 |
| `object_identity` — Object identity and lifetime | 35 |
| `one_shot_effects` — One-shot semantic effects | 187 |
| `players_format` — Players, relationships, and format state | 2 |
| `proposals` — Casting and activation proposals | 28 |
| `quantities` — Quantity and value expressions | 1 |
| `references` — References | 1 |
| `replacement_prevention` — Replacement and prevention | 23 |
| `triggers` — Triggers | 3 |

## Universal systems

| System | Status | Pieces | Blocking pieces |
|---|---|---:|---:|
| `action_legality_casting_activation_costs_mana` | `inventoried` | 126 | 6 |
| `combat` | `compositional` | 24 | 0 |
| `derived_characteristics_static_layers` | `inventoried` | 52 | 7 |
| `generic_triggers_stack_placement` | `inventoried` | 3 | 3 |
| `multiplayer_player_leaving_commander` | `compositional` | 7 | 0 |
| `objects_identity_zones_faces_copies` | `inventoried` | 43 | 1 |
| `replacement_prevention` | `inventoried` | 23 | 4 |
| `state_turn_loops_stabilization` | `inventoried` | 0 | 0 |
| `targets_modes_searches_references_choices` | `inventoried` | 16 | 10 |
| `typed_transactions_events_mutations` | `inventoried` | 303 | 84 |

## Highest current blocker leverage

| Piece | Class | Residuals | Sole blockers | Expected cards | Runtime | Assurance |
|---|---|---:|---:|---:|---|---|
| `residual.continuous_layer.continuous-effect-layers-and-dependencies` | `continuous_effects` | 4,808 | 2,102 | 2,102 | `absent` | `untested` |
| `residual.effect_clause.unparsed-clause-grammar` | `one_shot_effects` | 2,078 | 176 | 176 | `absent` | `untested` |
| `residual.activated_effect.unparsed-clause-grammar` | `one_shot_effects` | 1,711 | 131 | 131 | `absent` | `untested` |
| `residual.keyword_dependency.banding` | `keyword_mechanics` | 24 | 19 | 19 | `absent` | `untested` |
| `residual.effect_clause.typed-spell-additional-cost-clause` | `one_shot_effects` | 106 | 18 | 18 | `absent` | `untested` |
| `residual.activated_effect.put-onto-battlefield` | `one_shot_effects` | 209 | 17 | 17 | `absent` | `untested` |
| `residual.effect_clause.life-change` | `one_shot_effects` | 468 | 16 | 16 | `absent` | `untested` |
| `residual.replacement.damage-prevention` | `replacement_prevention` | 140 | 15 | 15 | `absent` | `untested` |
| `residual.keyword_dependency.start-your-engines` | `keyword_mechanics` | 40 | 14 | 14 | `absent` | `untested` |
| `residual.effect_clause.create-token` | `one_shot_effects` | 538 | 13 | 13 | `absent` | `untested` |
| `residual.activated_effect.create-token` | `one_shot_effects` | 283 | 13 | 13 | `absent` | `untested` |
| `residual.keyword_dependency.myriad` | `keyword_mechanics` | 23 | 11 | 11 | `absent` | `untested` |
| `residual.mechanic_dependency.fading-remaining-lifecycle` | `keyword_mechanics` | 17 | 11 | 11 | `absent` | `untested` |
| `residual.effect_clause.return` | `one_shot_effects` | 484 | 10 | 10 | `absent` | `untested` |
| `residual.keyword_dependency.umbra-armor` | `keyword_mechanics` | 15 | 10 | 10 | `absent` | `untested` |
| `residual.activated_effect.life-change` | `one_shot_effects` | 180 | 9 | 9 | `absent` | `untested` |
| `residual.keyword_dependency.extort` | `keyword_mechanics` | 18 | 9 | 9 | `absent` | `untested` |
| `residual.keyword_dependency.assist` | `keyword_mechanics` | 16 | 9 | 9 | `absent` | `untested` |
| `residual.keyword_dependency.enlist` | `keyword_mechanics` | 12 | 9 | 9 | `absent` | `untested` |
| `residual.effect_clause.add-mana` | `one_shot_effects` | 57 | 8 | 8 | `absent` | `untested` |
| `residual.keyword_dependency.split-second` | `keyword_mechanics` | 21 | 8 | 8 | `absent` | `untested` |
| `residual.keyword_dependency.learn` | `keyword_mechanics` | 13 | 8 | 8 | `absent` | `untested` |
| `residual.effect_clause.exile` | `one_shot_effects` | 525 | 7 | 7 | `absent` | `untested` |
| `residual.effect_clause.destroy-mass` | `one_shot_effects` | 141 | 7 | 7 | `absent` | `untested` |
| `residual.keyword_dependency.cipher` | `keyword_mechanics` | 15 | 7 | 7 | `absent` | `untested` |
| `residual.effect_clause.tap-state` | `one_shot_effects` | 298 | 6 | 6 | `absent` | `untested` |
| `residual.activated_effect.exile` | `one_shot_effects` | 291 | 6 | 6 | `absent` | `untested` |
| `residual.effect_clause.counter` | `one_shot_effects` | 221 | 6 | 6 | `absent` | `untested` |
| `residual.activated_effect.destroy-target` | `one_shot_effects` | 60 | 6 | 6 | `absent` | `untested` |
| `residual.keyword_dependency.fuse` | `keyword_mechanics` | 34 | 6 | 6 | `absent` | `untested` |

## Boundary

Inventory and classification are not implementation or trust. Universal systems remain conservatively below snapshot-complete until all required rules, pieces, rulings, and interactions close.
