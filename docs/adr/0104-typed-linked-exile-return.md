---
title: "ADR 0104: typed linked exile and return"
status: "ADR"
authoritative_source: "linked exile return compiler, model, coordinator and canonical zone owners"
verified: "2026-10-03"
audience: "rules, compiler and replay contributors"
maintenance: "hand-maintained"
adr_id: "0104"
decision_status: "accepted"
date: "2026-10-03"
---

# ADR 0104: typed linked exile and return

## Context

A battlefield exile followed by immediate or delayed return must follow the
new exiled object (CR 400.7j), not a stable physical card ID. A prospective-entry
replacement choice must not repeat an already committed exile. Multiple
selected objects move simultaneously within each movement instruction, while
the exile and return remain two different events.

## Decision

Use a closed compiler instruction pair through the existing typed domain-effect
handler and zone/attachment family. `linked_exile_return` records committed
exiled identities in one resolving-stack continuation and performs the separate
return or delayed scheduling phase. `return_linked_exiled_objects` validates
those identities when the ordinary delayed trigger resolves. The coordinator
delegates movement, entry counters, replacement ordering, layer grants and
delayed triggers to their existing owners. No Oracle prose is read at runtime.

The two operations receive an exact architecture baseline review. Engine
methods, engine size, direct and unowned state writes, card identity dispatch
and runtime prose debt do not grow. The model is a rules value boundary; immutable typed
entry-counter values cross the existing simultaneous-zone boundary.

Identical printed instructions in different modes receive distinct compiler
binding scopes. Optional blink uses one apply/decline choice around the
validated pair, not a decision for each phase. Existing optional payloads keep
their one-instruction meaning. New grammar preserves historical provenance
rejection rather than reinterpreting archived unsupported instructions.

## Alternatives

Inferring a return from the final zone or printed keyword loses causality and
incarnation. Two independent generic moves lack linked replacement resumption.
A separate blink event/replacement engine duplicates authority. None is used.

## Consequences

Immediate blink does not expose an intermediate priority or state-based-action
window. Tokens cannot return. Delayed returns preserve the original controller
and ignore departed/reentered exiled cards. Broader linked banishment,
conditional outcomes, transformed or face-down returns and attachment
restoration remain outside this capability.
