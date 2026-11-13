---
title: "ADR 0166: Exact automatic mana activation costs"
status: "ADR"
authoritative_source: "CR 601.2h, 602.2b and 605.3b and the activated counter-cost owner"
verified: "2026-11-12"
audience: "rules, mana and replay contributors"
maintenance: "hand-maintained"
adr_id: "0166"
decision_status: "accepted"
date: "2026-11-12"
---

# ADR 0166: Exact automatic mana activation costs

## Context

A tap mana ability can require counters from its own source. Automatic payment
previously represented the output and tap without committing that additional
cost. Explicit activation already used an identity-pinned counter payment.
Changing the automatic result also changes recorded authoritative state.

Automatic life payments were also applied after mana output despite being
activation costs. Printed controller damage correctly follows output. Fixing
that phase boundary must preserve records from the source-counter repair too.

## Decision

Both discovery and automatic commit delegate named source-counter payment to
the existing activated cost owner. A closed typed mana-mode cost carries the
validated counter specification. Commit pays it before producing the output.
An independent free mode remains available. Counter-kind selections remain
explicit actions until their choice is supplied.

New state and record-format data declare
`mana_source_counter_payment_version: 1`. Absent historical data retain their
execution and omit the field on serialization. Explicit malformed versions or
manifest/checkpoint mismatches reject. No old command or checkpoint is rewritten.

Life-cost ordering has its own `mana_cost_order_version: 1`. In current games,
automatic life costs commit before output while damage results follow output.
Absent historical data retain their earlier phase order. This marker remains
independent of source-counter payment so each recorded execution is preserved.

## Alternatives

Filtering every counter-bearing source from automatic payment would remove
deterministic legal payments. Subtracting counters directly in the mana owner
would duplicate counter-event and exact-payment authority. Applying the new
behavior to unmarked records would silently change historical state hashes.

## Consequences

Current automatic casts cannot reuse an exhausted colored counter mode.
Counter removal retains ordinary event, identity and transactional behavior.
Explicit activation pays once, and its nonreversible undo boundary is unchanged.
