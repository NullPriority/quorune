---
title: "Rules dependency queue"
status: "generated"
authoritative_source: "coverage/rules-dependency-queue.json"
verified: "3047c70b83465af81d30f7ca8169dd4a62496b16ed25dc7adbbdd6c9994b9be3"
audience: "rules, compiler, and engine contributors"
maintenance: "generated"
generated_source: "coverage/rules-dependency-queue.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_rules_scheduler.py --write"
---

# Rules dependency queue

Source fingerprint: `fa1b9110861499f46197ddb7f9aad291842b4fc9edddc564e8086b794211be67`

## Current top-level state

- Pinned rules: `3309`
- Queued rules: `2896`
- Subsystems: `21`
- Selected subsystem: `replacement-prevention`
- Selected batch: `counter-producer-replacement-closure`
- Selected cross-program work: `none`
- Selected work class: `none`

## Cross-program work selection

The rules batch remains dependency-ready, but final foreground work is reranked with deterministic CI, replay/privacy, architecture, runtime-text, interaction-assurance, compiler, and card-frontier evidence. A larger card gain cannot outrank a higher-priority correctness class.

Priority classes: `ci_correctness` → `replay_privacy_defect` → `architecture_owner_extraction` → `runtime_oracle_removal` → `interaction_assurance` → `rules_foundation` → `compiler_harvest` → `card_family` → `architecture_debt`

| Rank | State | Candidate | Class | Members | Contexts | Complete cards | Residuals | Cards/hour | Runtime text | Direct writes |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | complete | `ci:compact-card-dependency-closure` | `ci_correctness` | 1 | 0 | 0 | 0 | unknown | 0 | 0 |
| 2 | complete | `correctness:replay-privacy-recovery` | `replay_privacy_defect` | 1 | 0 | 0 | 0 | unknown | 0 | 0 |
| 3 | complete | `architecture:dedicated-owner-extraction` | `architecture_owner_extraction` | 1 | 0 | 0 | 0 | unknown | 0 | 0 |
| 4 | complete | `assurance:critical-interaction-recovery` | `interaction_assurance` | 1 | 0 | 0 | 0 | unknown | 0 | 0 |
| 5 | blocked | `frontier:continuous_layer:continuous-effect-layers-and-dependencies` | `rules_foundation` | 1 | 0 | 3786 | 8440 | 62.065574 | 0 | 0 |
| 6 | blocked | `interaction-implementation:residual.replacement.replacement-applicability` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 7 | blocked | `interaction-implementation:residual.replacement.self-replacement-and-prevention-ordering` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 8 | blocked | `interaction-implementation:residual.replacement.damage-prevention` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 9 | blocked | `interaction-implementation:residual.replacement.regeneration` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 10 | blocked | `interaction-implementation:residual.continuous_layer.affected-player-ordering` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 11 | blocked | `interaction-implementation:residual.continuous_layer.continuous-effect-layers-and-dependencies` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 12 | blocked | `interaction-implementation:residual.duration.until-end-of-turn` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 13 | blocked | `interaction-implementation:residual.target_or_choice.target-predicate` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 14 | blocked | `interaction-implementation:residual.target_or_choice.conditional-effect` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 15 | blocked | `interaction-implementation:residual.event_binding.intervening-if-and-reflexive-trigger-grammar` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 16 | blocked | `interaction-implementation:residual.event_binding.normalized-event-binding` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 17 | blocked | `interaction-implementation:residual.static_clause.broader-evasion-and-group-constraints` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 18 | blocked | `interaction-implementation:residual.static_clause.conditional-declaration-predicates` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 19 | blocked | `interaction-implementation:residual.static_clause.temporary-declaration-restrictions` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 20 | blocked | `interaction-implementation:residual.activated_cost.complete-alternate-additional-cost-grammar` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 21 | blocked | `interaction-implementation:residual.activated_cost.restricted-payment-predicates` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 22 | blocked | `interaction-implementation:residual.target_or_choice.multiple-targets` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 23 | blocked | `interaction-implementation:residual.target_or_choice.divided-damage-allocation` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 24 | blocked | `interaction-implementation:residual.target_or_choice.multiple-damage-recipients` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 25 | blocked | `interaction-implementation:residual.target_or_choice.random-outcome` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 26 | blocked | `rules:counter-producer-replacement-closure` | `rules_foundation` | 1 | 0 | unknown | unknown | unknown | 0 | 0 |
| 27 | blocked | `bundle:fixed-token-creation-contexts` | `compiler_harvest` | 2 | 4 | 39 | 167 | 2.294118 | 0 | 0 |
| 28 | blocked | `bundle:fixed-exile-contexts` | `compiler_harvest` | 2 | 4 | 15 | 106 | 1.0 | 0 | 0 |
| 29 | blocked | `frontier:effect_clause:typed-spell-additional-cost-clause` | `compiler_harvest` | 1 | 3 | 17 | 106 | 0.809524 | 0 | 0 |
| 30 | blocked | `frontier:effect_clause:unparsed-choose-one` | `compiler_harvest` | 1 | 3 | 0 | 261 | 0.0 | 0 | 0 |
| 31 | blocked | `architecture:engine-mutation-and-specificity-debt` | `architecture_debt` | 1 | 0 | 0 | 0 | unknown | 0 | 54 |

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
