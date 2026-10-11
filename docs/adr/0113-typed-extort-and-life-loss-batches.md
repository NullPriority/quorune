---
title: "ADR 0113: typed Extort and simultaneous life-loss batches"
status: "ADR"
authoritative_source: "compiler/extort_nodes.py, rules/extort.py, semantic_choices/extort.py, and semantic_choices/life_intent_commit.py"
verified: "2026-10-10"
audience: "rules, compiler, replay and architecture contributors"
maintenance: "hand-maintained"
adr_id: "0113"
decision_status: "accepted"
date: "2026-10-10"
---

# ADR 0113: typed Extort and simultaneous life-loss batches

## Context

Extort combines a cast trigger, one optional hybrid payment, simultaneous loss
for opponents, and a later gain derived from actual life lost. A fixed drain
amount or the number of opponents cannot represent prevented or replaced loss.
Combining the gain with the loss in one batch also changes printed ordering.
Existing optional payments, life transactions and replacement continuations
already own the individual actions.

## Decision

Add the closed `extort` operation to the existing semantic choice registry.
Each printed instance lowers to an independent battlefield cast trigger.
The trigger controller chooses one payable white or black mana vector or
declines; the canonical payment intent commits only that chosen vector.

The accepted continuation executes a typed `LifeLossBatchIntent` through the
existing life-change batch owner. Its distinct opponent identities and fixed
amount are validated, and replacement ordering uses the existing semantic
preparation journal. A typed result binding carries the actual resolved loss
returned by the canonical batch into the next cursor stage. Triggered
consequences do not resolve between these two instructions. The later gain
uses the canonical life-change intent, so gain replacements and their pending
choices preserve the already committed loss.

Extract life commitment from the general semantic intent host into a cohesive
life-intent delegate, and extract resource/life dispatch from the general
executor. Specialized node-shape routing and stack-static keyword lowering use
small existing-registry delegates. These extractions preserve older paths and
prevent growth of oversized hosts, compiler modules and the executor function.
Default event-source discovery uses a canonical physical-object order before
assigning trigger references. Explicit supplied source batches retain their
order, and the controller still chooses the ordering of simultaneous triggers.
This prevents checkpoint dictionary ordering from changing pending decisions
or replay hashes when independent Extort sources trigger together.

The exact architecture baseline admits only this reviewed universal operation.
Engine methods, direct state writes, runtime Oracle interpretation, identity
dispatch and oversized-symbol ceilings receive no growth allowance. Every new
module retains its generated ownership classification and dependency direction.

## Alternatives

Expanding Extort into a fixed opponent count would overstate prevented loss.
Using the historical fixed-drain operation would perform a different gain and
order. A new payment, event or life registry would duplicate authority. Runtime
Oracle parsing cannot establish capability closure or deterministic behavior.

## Consequences

Extort stages use immutable typed continuation fields. Clients can submit only
the published payment choice; they cannot supply the loss batch or its result.
Unknown stages, malformed amounts, duplicated players and unsupported sibling
programs fail closed. Existing record shapes and single-life intent behavior
are preserved. External unrepresented life-prohibition producers remain
unsupported until their own capability contracts close.

## Verification

Focused witnesses exercise white and black payment, decline, unpayable costs,
wrong principals, total multiplayer loss, a canonical prevented-loss boundary,
gain multipliers, pending payment and replacement save/load, independent
instances, exact replay and mutations that replace actual total loss with a
fixed gain. Adjacent life/payment and special-node consumers retain their
directly affected diagnostics, followed by complete public certification.
