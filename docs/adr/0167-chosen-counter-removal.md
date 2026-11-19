---
title: "ADR 0167: Chosen counter removal"
status: "ADR"
authoritative_source: "CR 122.1, 608.2d, 609.3 and the canonical counter-removal owner"
verified: "2026-11-15"
audience: "rules, counter and replay contributors"
maintenance: "hand-maintained"
adr_id: "0167"
decision_status: "accepted"
date: "2026-11-15"
---

# ADR 0167: Chosen counter removal

## Context

Removing an unnamed counter requires a choice among the kinds present when the
instruction resolves. A counterless permanent is still a legal target. Existing
fixed named removals already own counter transitions, journaling and subsequent
state-based actions.

## Decision

Add one operation, `remove_chosen_counter`, with an explicit version-one
instruction. Its closed grammar removes one counter from a target permanent or
nonland permanent. The resolving controller chooses a currently present kind
through the existing scalar-choice continuation. The choice pins the original
recipient incarnation and validates both the offered kinds and current counts.
An empty recipient continues without asking for an impossible choice.

Completion returns one existing `RemoveCountersIntent`. Counter removal retains
its canonical planning, mutation, events and state-based-action ownership. The
choice introduces no direct state writes, new engine method, runtime Oracle
parsing or card-identity dispatch. Optional wrappers and printed activation
costs retain their existing owners. Player counters, larger quantities and
arbitrary-counter activation costs remain outside this grammar.

The reviewed baseline allowance adds only this registered operation. All other
structural ceilings, identities and allowances remain exactly as recorded.

## Alternatives

Choosing a kind during target announcement would use the wrong time and could
remove an unavailable counter after responses. Direct counter dictionary writes
would duplicate mutation and event authority. Broadening the named-removal
codec would change historical descriptors and obscure its fixed-kind contract.

## Consequences

The new choice uses public counter information and the authenticated resolving
controller. Pending continuation state supports ordinary save/load and exact
replay. Unknown kinds, changed recipients and malformed responses fail before
mutation. Existing fixed named and all-counter instructions retain their
serialized form and execution semantics.
