---
title: "ADR 0116: typed fixed energy payments"
status: "ADR"
authoritative_source: "fixed payment choice, energy intent and counter transaction owners"
verified: "2026-10-14"
audience: "rules, compiler and replay contributors"
maintenance: "hand-maintained"
adr_id: "0116"
decision_status: "accepted"
date: "2026-10-14"
---

# ADR 0116: typed fixed energy payments

## Context

Resolving optional energy payments consume a public player resource before a
separately represented effect. Spending energy is a cost and must require the
entire amount; it must not inherit counter-placement replacement behavior or
the counter transaction's general truncation of removal at zero.

## Decision

The existing fixed payment descriptor admits a positive printed energy amount.
Its existing choice owner advertises pay or decline from immutable player-counter
facts and rechecks the current resource on acceptance. A dedicated immutable
`PayEnergyIntent` enters the existing resource-intent executor. The rules-layer
energy coordinator validates the complete amount, delegates mutation to the
canonical counter transaction, and journals the payment before the already
typed consequence resumes. No engine method or second counter writer is added.

The event-aware payment compiler keeps original source-incarnation references
inside the consequence. An unavailable original counter recipient becomes an
empty group through the existing strict counter validator; it cannot redirect
the result to a returned incarnation. All other instruction fields still
validate before a no-op can proceed.

## Alternatives

Negative counter-placement effects would apply the wrong replacement semantics.
Direct choice-handler writes would violate the immutable handler boundary.
Adding a separate energy choice registry would duplicate authentication,
continuation, projection and replay. The existing payment and counter owners
already supply those boundaries.

## Consequences

Fixed energy spending has its own required capability and exact resource,
rollback, source-departure and replay evidence. Existing payment payloads retain
their serialization and dispatch. Variable energy, repeated payments, mixed
resource prices, reflexive triggers and independently unsupported consequences
remain residual.
