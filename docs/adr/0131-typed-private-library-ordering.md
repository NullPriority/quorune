---
title: "ADR 0131: typed private library ordering"
status: "ADR"
authoritative_source: "fixed library order model and existing ordering/shuffle intents"
verified: "2026-10-27"
audience: "compiler, semantic choice, library and replay contributors"
maintenance: "hand-maintained"
adr_id: "0131"
decision_status: "accepted"
date: "2026-10-27"
---

# ADR 0131: typed private library ordering

## Context

A fixed look-and-reorder instruction keeps every inspected card on top. It is
not Scry, which permits a bottom partition and produces a distinct occurrence.
The historical `look_reorder_top` choice has a permissive input boundary and
no fine-grained capability minimum. Its persisted form must remain readable.

## Decision

Add a distinct closed controller-order instruction and capability. Reuse the
existing private ordering choice and `ReorderLibraryTopIntent` mutation owner.
Pin the inspected top set and each logical incarnation in the new continuation.
The selected order must be an exact permutation of that current pinned set.
Principal projection, empty and short libraries, rollback and pending replay
retain their existing owners.

Represent explicit controller shuffle as a separate exact leaf with its own
capability and the existing `ShuffleLibraryIntent`. The ordinary optional
effect and ordered composition owners express look/order, optional shuffle
and later draw. No card identity or special Ponder executor selects behavior.

Infer both minima from the executable descriptors, independently of coverage.
The old operation, handler identifier and continuation behavior remain intact.

## Alternatives

Using Scry changes legal choices and trigger behavior. Relaxing the historical
handler in place changes its persisted contract. A combined card-specific
ordering/shuffle/draw executor duplicates existing composition and mutation.

## Consequences

New positive instructions have bounded counts, one controller, and exact
schemas. Variable counts, opponent-library choices and unowned result siblings
remain residual. Native evidence must distinguish private order from Scry,
shuffle from decline, source departure, exact choice cardinality, current
incarnation, once-only continuation, and principal-correct replay.
