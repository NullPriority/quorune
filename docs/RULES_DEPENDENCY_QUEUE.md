---
title: "Rules dependency queue"
status: "generated"
authoritative_source: "coverage/rules-dependency-queue.json"
verified: "1731a96770b7e63e6bae7ba3d283b2499e0585247cdcbc5751b355e4cfdbf238"
audience: "rules, compiler, and engine contributors"
maintenance: "generated"
generated_source: "coverage/rules-dependency-queue.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_rules_scheduler.py --write"
---

# Rules dependency queue

Source fingerprint: `73e443600147b42ed43246e39bc43f214fbac2ae082106fa0ebf5b22b2b99784`

## Current top-level state

- Pinned rules: `3309`
- Queued rules: `2869`
- Subsystems: `21`
- Selected subsystem: `replacement-prevention`
- Selected batch: `counter-producer-replacement-closure`
- Selected cross-program work: `none`
- Selected work class: `none`
- Selected work state: `none`
- Measurement grants gameplay trust: `not_applicable`

## Cross-program work selection

The rules batch remains dependency-ready, but final foreground work is reranked with deterministic CI, replay/privacy, architecture, runtime-text, interaction-assurance, compiler, and card-frontier evidence. A larger card gain cannot outrank a higher-priority correctness class.
When no implementation candidate is eligible, one bounded cohort measurement may be selected. Its upper bounds remain non-executable and grant no gameplay trust until the declared upgrade evidence is generated.

Priority classes: `ci_correctness` → `replay_privacy_defect` → `prohibited_runtime_semantics` → `architecture_owner_or_mutation_defect` → `interaction_assurance` → `rules_foundation` → `compiler_harvest` → `card_family`

| Rank | Selection | Work state | Implementation eligible | Candidate | Class | Members | Contexts | Complete cards | Residuals | Cards/hour | Runtime text | Direct writes |
|---:|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | complete | implementation | false | `ci:compact-card-dependency-closure` | `ci_correctness` | 1 | 0 | 0 | 0 | unknown | 0 | 0 |
| 2 | complete | implementation | false | `ci:materialize-harvest-outcome` | `ci_correctness` | 1 | 0 | 0 | 0 | unknown | 0 | 0 |
| 3 | complete | implementation | false | `correctness:replay-privacy-recovery` | `replay_privacy_defect` | 1 | 0 | 0 | 0 | unknown | 0 | 0 |
| 4 | complete | implementation | false | `architecture:dedicated-owner-extraction` | `architecture_owner_or_mutation_defect` | 1 | 0 | 0 | 0 | unknown | 0 | 0 |
| 5 | blocked | implementation | false | `architecture:engine-mutation-and-specificity-debt` | `architecture_owner_or_mutation_defect` | 1 | 0 | 0 | 0 | unknown | 0 | 54 |
| 6 | complete | implementation | false | `assurance:critical-interaction-recovery` | `interaction_assurance` | 1 | 0 | 0 | 0 | unknown | 0 | 0 |
| 7 | blocked | implementation | false | `frontier:continuous_layer:continuous-effect-layers-and-dependencies` | `rules_foundation` | 1 | 0 | 2302 | 5328 | 37.737705 | 0 | 0 |
| 8 | blocked | implementation | false | `interaction-implementation:residual.replacement.replacement-applicability` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 9 | blocked | implementation | false | `interaction-implementation:residual.replacement.self-replacement-and-prevention-ordering` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 10 | blocked | implementation | false | `interaction-implementation:residual.replacement.damage-prevention` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 11 | blocked | implementation | false | `interaction-implementation:residual.replacement.regeneration` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 12 | blocked | implementation | false | `interaction-implementation:residual.card_form.ordinary-saga-chapter-event-binding` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 13 | blocked | implementation | false | `interaction-implementation:residual.continuous_layer.continuous-effect-layers-and-dependencies` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 14 | blocked | implementation | false | `interaction-implementation:residual.continuous_layer.affected-player-ordering` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 15 | blocked | implementation | false | `interaction-implementation:residual.target_or_choice.target-predicate` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 16 | blocked | implementation | false | `interaction-implementation:residual.target_or_choice.conditional-effect` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 17 | blocked | implementation | false | `interaction-implementation:residual.duration.until-end-of-turn` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 18 | blocked | implementation | false | `interaction-implementation:residual.target_or_choice.multiple-targets` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 19 | blocked | implementation | false | `interaction-implementation:residual.event_binding.intervening-if-and-reflexive-trigger-grammar` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 20 | blocked | implementation | false | `interaction-implementation:residual.event_binding.normalized-event-binding` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 21 | blocked | implementation | false | `interaction-implementation:residual.static_clause.broader-evasion-and-group-constraints` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 22 | blocked | implementation | false | `interaction-implementation:residual.static_clause.conditional-declaration-predicates` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 23 | blocked | implementation | false | `interaction-implementation:residual.static_clause.temporary-declaration-restrictions` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 24 | blocked | implementation | false | `interaction-implementation:residual.activated_cost.complete-alternate-additional-cost-grammar` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 25 | blocked | implementation | false | `interaction-implementation:residual.activated_cost.restricted-payment-predicates` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 26 | blocked | implementation | false | `interaction-implementation:residual.target_or_choice.divided-damage-allocation` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 27 | blocked | implementation | false | `interaction-implementation:residual.event_binding.ordinary-saga-chapter-event-binding` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 28 | blocked | implementation | false | `interaction-implementation:residual.target_or_choice.multiple-damage-recipients` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 29 | blocked | implementation | false | `interaction-implementation:residual.target_or_choice.random-outcome` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 30 | blocked | implementation | false | `rules:counter-producer-replacement-closure` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 31 | blocked | implementation | false | `frontier:effect_clause:typed-spell-additional-cost-clause` | `compiler_harvest` | 1 | 3 | 18 | 106 | 0.857143 | 0 | 0 |
| 32 | blocked | implementation | false | `bundle:fixed-exile-contexts` | `compiler_harvest` | 2 | 4 | 0 | 18 | 0.0 | 0 | 0 |
| 33 | blocked | implementation | false | `bundle:fixed-token-creation-contexts` | `compiler_harvest` | 2 | 4 | 0 | 18 | 0.0 | 0 | 0 |
| 34 | blocked | implementation | false | `frontier:effect_clause:unparsed-choose-one` | `compiler_harvest` | 1 | 3 | 0 | 224 | 0.0 | 0 | 0 |
| 35 | blocked | implementation | false | `bundle:fixed-cast-lifecycles` | `compiler_harvest` | 1 | 2 | 0 | 0 | 0.0 | 0 | 0 |
| 36 | blocked | implementation | false | `bundle:public-static-cast-cost-modifiers` | `compiler_harvest` | 1 | 1 | 0 | 0 | 0.0 | 0 | 0 |

Selected reason: No serious candidate currently meets the generated eligibility policy; retain visible deferred pressure and recompute after the next measured frontier classification.

## Top blockers

- Inventory every represented permanent- and player-counter producer and identify which paths still bypass the canonical counter-placement owner.
- Route one coherent reusable producer family through the immutable resumable counter-placement transaction without adding direct GameState writes.
- Preserve cost timing, entry timing, simultaneous APNAP ordering, rollback, privacy, and exact replay for migrated producers.
- Add generic CardProgram lowering and precise source spans where the migrated family originates in Oracle text.
- Add focused positive, negative, interaction, multiplayer, rollback, replay, and killed implementation-mutation evidence for the migrated boundary.

Complete rule, subsystem, dependency, classification, and selected-batch data plus complete readiness, blocker-card, architecture, interaction, and reranking fields for every serious candidate are in the [machine-readable rules queue](../coverage/rules-dependency-queue.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_rules_scheduler.py --write
```
