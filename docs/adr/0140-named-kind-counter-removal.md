---
title: "ADR 0140: named-kind counter removal"
status: "ADR"
authoritative_source: "typed counter-removal plans and semantic descriptors"
verified: "2026-10-29"
audience: "rules, compiler and replay contributors"
maintenance: "hand-maintained"
adr_id: "0140"
decision_status: "accepted"
date: "2026-10-29"
---

# ADR 0140: named-kind counter removal

## Context

Removing every counter of one named kind differs from removing every kind or
requesting a fixed amount. A large numeric sentinel loses that distinction,
while a generic scalar amount would widen unrelated result slots and needs a
separate zero-value policy. Source-self instructions must also retain their
original incarnation without becoming targeted effects.

## Decision

Extend the existing `remove_all_counters` operation with a closed version-two
descriptor containing one canonical `counter` name. The initial compiler
production admits mandatory source-self instructions on independently
represented battlefield triggers. It uses the existing source-incarnation
reference and no target schema. Unknown fields, arbitrary amounts and ambiguous
event-object pronouns remain rejected.

The immutable all-counter intent and plan carry an optional counter-name
selector. Absence preserves the existing all-kinds mode and descriptor bytes.
Named mode validates the complete counter map, snapshots only the selected
positive kind, and revalidates that selection and object identity before the
same canonical counter-state commit. Zero matching counters is an empty plan.
An unavailable source incarnation produces a validated no-op.

The handler accepts the old descriptor unchanged. Named mode requires a
separate capability in addition to the existing removal transaction owner.
No new operation, mutation owner, runtime parser or counter registry is added.

## Alternatives

- A very large fixed amount obscures the requested operation and its result.
- A generic dynamic amount expands authority beyond one named-kind selection.
- The existing named-counter-count descriptor belongs to placement and doubling;
  reusing it for removal would still need a distinct removal contract and would
  obscure the complete selected-kind snapshot in the typed plan.
- A second counter-removal operation duplicates the existing snapshot and
  transaction path.

## Consequences

Unrelated counter kinds remain unchanged. A departed and returned source cannot
receive a pending mutation. The existing fixed-count and all-kinds descriptors,
their replay interpretation, and exact removal costs retain their contracts.
Costs, linked removed-count results, broad recipient sets, counter movement and
unsupported state-trigger heads require their own independently reviewed work.
