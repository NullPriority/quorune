---
title: "ADR 0163: Ordered hybrid mana payments"
status: "ADR"
authoritative_source: "CR 107.4e, 117.1d and 605.3 and the canonical mana provenance owner"
verified: "2026-11-08"
audience: "compiler, rules and replay contributors"
maintenance: "hand-maintained"
adr_id: "0163"
decision_status: "accepted"
date: "2026-11-08"
---

# ADR 0163: Ordered hybrid mana payments

## Context

A fixed hybrid tap filter has an input cost and a choice of output. Another
mana ability can supply the input during payment. The input and the enclosing
cost can have different spending permissions and provenance requirements.

## Decision

The fixed-output descriptor carries the same two typed price options as its
activation cost schema. Discovery gives each priced mode its ability, current
cost option, and spending context. The shared affordability and payment
owner uses the existing source selection for free modes and searches ordered
steps when a filter is needed.

The ordered search uses an isolated pool and the canonical provenance
operations. A step spends the input in its own context before adding its
output. A source can participate once in the automatic plan. The commit
owner revalidates the current source, output, and cost option before spending
the declared input. Explicit ordered plans use the same commit path.

## Alternatives

Adding net output to an aggregate pool could fund a cycle before its first
activation can be paid. Requiring an earlier priority action would omit mana
abilities that CR 117.1d permits during payment.

## Consequences

Unseeded filters produce no colored mana through the planner. Eligible
restricted and snow lots retain their provenance through payment.
Separately priced forms outside the fixed hybrid tap shape continue to use
explicit activation. Historical source-and-output steps have no input price.
