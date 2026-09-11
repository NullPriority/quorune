---
title: "ADR 0097: typed fixed creature-power damage"
status: "ADR"
authoritative_source: "fixed creature-power compiler, capability shape, source-LKI owner, and canonical damage transaction"
verified: "2026-09-11"
audience: "rules, compiler, damage, replay, and architecture maintainers"
maintenance: "hand-maintained"
adr_id: "0097"
decision_status: "accepted"
date: "2026-09-11"
---

# ADR 0097: typed fixed creature-power damage

## Context

Fight and one-way damage equal to a creature's power share target
revalidation, characteristic evaluation, source incarnation, damage
replacement, prevention, result, and replay concerns. Fight requires both
participants to remain creatures when it resolves. A one-way instruction can
instead use the departed source's last known power when the source itself
dealt the damage. Implementing those wordings through ordinary fixed-damage
effects would either lose that temporal distinction or duplicate the canonical
damage transaction.

The v187 harvest registered a strict runtime handler and capability-closed
compiler shape, but omitted `creature_power_damage` from the canonical
semantic-program operation inventory. The broad semantic-registry sentinel
therefore detected an unreviewed universal operation after the squash merge.

## Decision

Register `creature_power_damage` as one reviewed universal semantic operation.
Its closed instruction distinguishes Fight from one-way power damage, identifies
source and recipient placeholders, and accepts no arbitrary query, callback,
card identity, or Oracle prose. Compiler-owned target groups and capability
validation constrain every emitted shape before runtime dispatch.

`quorune.creature_power_damage` owns current exact-power evaluation, source
last-known-information capture, and logical-incarnation checks. Fight uses only
two current battlefield creatures and does nothing if either participant is
absent or no longer a creature. One-way source damage may use the captured
power of the departed incarnation, while a re-entered incarnation cannot stand
in for the old source.

The effect-runtime handler delegates each represented instruction to the
existing damage proposal and batch resolution owners. Replacement ordering,
prevention, Lifelink, damage results, state-based actions, rollback, projection,
save/load, and exact replay remain under those canonical owners. The operation
adds no `CommanderEngine` method and no direct or unowned `GameState` write.

The impact policy treats the semantic validity inventory, effect-operation
contract, and every effect-runtime module as inputs to the semantic-handler
registry sentinel. A future universal operation therefore reaches that
invariant on its feature PR instead of first appearing in exact-main broad CI.

## Alternatives

- Lower Fight into two unrelated fixed-damage operations. Rejected because
  simultaneous Fight damage and self-Fight semantics would be lost.
- Infer a source's old power from its new incarnation. Rejected because a zone
  change creates a new object and cannot satisfy the prior source reference.
- Parse printed text in the damage handler. Rejected because CardProgram nodes,
  not runtime Oracle prose, are the authoritative behavior boundary.
- Weaken the semantic-registry sentinel to accept any registered handler.
  Rejected because the explicit operation inventory is the Phase 1 review gate
  for additions to the universal executor.

## Consequences

Capability-closed Fight and one-way creature-power damage now have one explicit
semantic validity identity shared with their compiler and runtime owner. Their
damage composes through the existing affected-player replacement choice and
canonical result pipeline. Public targets, calculated power, damage, choices,
and results remain public; internal physical and logical identities remain
absent from pilot projections.

Characteristic-defining or cyclic power, unsupported type-changing
interactions, arbitrary quantities, divided or multiplied damage, mass or
random recipients, copied or text-changed instructions, unsupported target
domains, modal branches, and compound riders remain fail-closed. Trust for
dynamic characteristic interactions remains limited to the existing
cycle-safe exact-characteristic boundary.

## Removal condition

Retire this operation only if a successor preserves closed pre-mutation
validation, simultaneous Fight damage, self-Fight behavior, current-creature
revalidation, exact source LKI, logical-incarnation pinning, canonical damage
replacement and prevention, Lifelink and result handling, rollback, privacy,
save/load, and exact replay without runtime Oracle parsing, card identity, or
direct state mutation.
