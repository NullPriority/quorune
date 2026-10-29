---
title: "ADR 0129: public-state trigger observation"
status: "ADR"
authoritative_source: "typed state subscription and ordinary trigger discovery"
verified: "2026-10-27"
audience: "rules, compiler, trigger and replay contributors"
maintenance: "hand-maintained"
adr_id: "0129"
decision_status: "accepted"
date: "2026-10-27"
---

# ADR 0129: public-state trigger observation

## Context

CR 603.8 triggers as soon as a public state is true, including during
resolution. It suppresses another occurrence of that ability until its pending
instance leaves the stack. Checking only before priority misses a condition
that becomes true and then false during sequential instructions. Treating the
condition as an intervening if clause incorrectly cancels an already queued
result when a player changes that state in response.

## Decision

Represent one versioned subscription containing an existing immutable public
condition. The initial result delegates mandatory source sacrifice to the
existing source-maintenance owner with no payment. Compile complete source
spans and bind the subscription and result capabilities independently at
runtime. Unsupported conditions and result bodies remain residual.

Observe through completed single-object or simultaneous zone transitions,
committed semantic occurrences, instruction completion and pre-SBA
synchronization. Zone observation also covers moves that publish no ordinary
semantic occurrence, such as moving a casting card from hand to stack before
paying later costs. Simultaneous zone changes retain their complete
batch boundary. Observation collects ordinary trigger occurrences; it neither
resolves an effect nor runs another scheduler. Normal APNAP placement and
current ability participation remain authoritative.

Suppress a repeated occurrence using the ordinary pending batches and stack,
keyed by semantic ability, source object and its captured logical identity.
There is no additional GameState latch or journal. A resolving instance stays
on the stack through replacement and choice suspension. A new incarnation may
trigger independently of an old incarnation's pending instance. Resolution
does not re-evaluate the state condition.

Use a derived, invalidated event lookup inside the existing semantic registry
so games without a state subscription avoid scanning all programs on every
observation. Loading, replacement and removal invalidate that index. It is not
serialized and does not affect historical registry payloads or fingerprints.

## Alternatives

Priority-only checks miss transient states. An event-specific list of card
names or independent watchers duplicates rules authority. A persistent latch
duplicates the pending occurrences and creates save/load synchronization work.
An intervening condition changes resolution behavior. A separate sacrifice
executor duplicates the existing incarnation, control and replacement checks.

## Consequences

The family shares existing query evaluation, trigger batching and zone mutation
owners. New observation boundaries require native temporal tests for transient
conditions, simultaneous changes, source identity, countering and retriggering,
as well as ordinary principal projection, rollback and exact replay evidence.
