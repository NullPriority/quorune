---
title: "ADR 0165: Temporary cast Cascade grants"
status: "ADR"
authoritative_source: "CR 601.2a, 601.2i, 611.2a, 611.2f, 702.85 and the canonical cast transaction"
verified: "2026-11-09"
audience: "compiler, rules, casting and replay contributors"
maintenance: "hand-maintained"
adr_id: "0165"
decision_status: "accepted"
date: "2026-11-09"
---

# ADR 0165: Temporary cast Cascade grants

## Context

A resolved ability can grant Cascade to the next eligible spell or to each
hand cast during the current turn. The ability belongs to the announced spell
before payment. Its trigger waits for cast completion. Repeated grants create
separate instances, and one effect can reserve independent spell types.

## Decision

Compile `grant_cast_cascade` as a closed controller result in the state and
permissions owner. Store the active turn and the ordered scopes. The cast
proposal supplies the announced type and origin. Initial placement binds the
matching instances to the spell, and cast completion consumes the reserved
one-shot uses after payment. The existing action transaction restores the
spell and window together if the cast fails. Cleanup expires unused grants.

The stack characteristic owner projects the bound instances through layer
six. The ordinary Cascade owner collects each instance at cast completion
and resolves it through its existing exile and optional cast choice.

## Alternatives

A payment-kind set would combine repeated instances and consume all scopes on
the next successful cast. A trigger added directly after payment would miss
the characteristic present during announcement. The controller window and
spell binding represent those two stages separately.

## Consequences

The architecture review adds one operation. The window and spell binding are
serialized when the represented result is used. Runtime validation checks the
controller, scopes, current turn, and reserved uses. The existing choice and
projection owners govern the Cascade reveal and optional cast.

## Removal condition

A successor preserves announced characteristics, independent consumption,
ordered multiplicity, cleanup, rollback, principal projection, and exact replay.
