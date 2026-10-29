---
title: "ADR 0130: casting source placement before costs"
status: "ADR"
authoritative_source: "canonical casting commit, source placement version and Game Record provenance"
verified: "2026-10-27"
audience: "casting, rules, persistence and replay contributors"
maintenance: "hand-maintained"
adr_id: "0130"
decision_status: "accepted"
date: "2026-10-27"
---

# ADR 0130: casting source placement before costs

## Context

CR 601.2a places the casting card on the stack before cost payment. The former
commit path paid costs before creating its spell item. A hand-size-dependent
creature can therefore have a different toughness during payment. A state
trigger can fire during that interval even if a later sacrifice removes that
creature. Mana production based on such characteristics also changes when the
casting card leaves hand, so affordability must use the same facts as payment.

Historical checkpoints and accepted-command records bind internal transition
order through timestamps and hashes. Silently changing that order breaks the
explicit historical replay contract.

## Decision

New GameState instances opt into `cast_source_placement_version: 1`. Place a
real spell item with its declared source, face, modes, targets and X before
paying costs. Complete that same item with actual payment and cost facts after
payment succeeds. Consume one-shot casting grants once, on successful final
completion. Existing command transactions own rollback of an unsuccessful cast.
The pure declared-choice and paid-cost context builder is separate from this
mutation owner; preparation and finalization share its exact serialization.

During affordability reads, use an immutable prospective source/player-zone
view and bind pure characteristic and mana-source reads to that view. Keep
nonmana option construction and accepted payment on their existing owners.
No state mutation, ghost stack item, alternate payer or independent legality
list participates in this preview.

Bind the placement marker in Game Record format provenance and require it to
match the initial checkpoint. Historical absence serializes as absence and
retains the former source-placement order. Invalid versions fail closed.

## Alternatives

Observing only after payment misses a genuine transient state. Special-casing
hand counts does not cover layered characteristics or other source zones.
Creating a placeholder spell unrelated to the final spell duplicates source
authority. An unconditional transition-order change invalidates older records.

## Consequences

The canonical source transition and payment remain separately observable
stages of one atomic cast. Exact tests cover modern and historical order,
additional-cost rollback, marker provenance, pending replay and dynamic mana
availability. Existing casting features need their directly affected witnesses
before the final database-backed certification.
