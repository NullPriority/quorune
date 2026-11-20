---
title: "ADR 0171: Query-bounded library searches"
status: "ADR"
authoritative_source: "CR 107.1b, 109.5, 608.2h and 701.23"
verified: "2026-11-16"
audience: "compiler, search and replay contributors"
maintenance: "hand-maintained"
adr_id: "0171"
decision_status: "accepted"
date: "2026-11-16"
---

# ADR 0171: Query-bounded library searches

## Context

A search maximum derived from current public state is evaluated when its
instruction applies. Found lands can change that state. Recomputing the value
during placement or accepting an unowned literal would change the instruction
and could disagree with an already issued private offer.

## Decision

Use explicit search version eight with a closed `QueryBoundLibrarySearchSpec`.
Reuse the existing public quantity producer and stack amount cache, binding the
resolved value to the original controller and instruction pointer before any
private choice. Save both the typed expression and its resolved binding, and
validate that the cached value still agrees when accepting or resuming it.

Delegate candidates, private offers, legal cardinality, movement, replacement
continuations and whole-library shuffle to the existing search and zone owners.
Only basic lands, the represented public quantities, and tapped battlefield or
revealed-hand placement are accepted. Zero remains a search followed by shuffle.
The frozen maximum has no arbitrary fixed-search upper limit.

Versions one through seven retain their historical payloads and replay meaning.
No operation, runtime Oracle parser, behavior registry or card identity switch
is introduced. Payload-derived capability requirements independently retain the
new authority when descriptive coverage is removed.

## Alternatives

- Reusing the fixed-count grammar would impose an unrelated upper limit.
- Recomputing after entry would count newly found lands and broaden legality.
- A client-supplied literal would remove the typed quantity and source binding.
- A new search implementation would duplicate hidden-information and zone rules.

## Consequences

Existing public quantity owners define which counts are supported. Hidden-zone
counts, source last-known power, speed, distinct powers and cross-player
comparisons remain outside this grammar. Complete programs must independently
close every additional printed instruction, trigger and static allowance.
