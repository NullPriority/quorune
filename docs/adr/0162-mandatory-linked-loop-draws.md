---
title: "ADR 0162: Mandatory linked loop draws"
status: "ADR"
authoritative_source: "CR 104.4b, 732.4 and 732.5 and the forced linked trigger witness"
verified: "2026-11-08"
audience: "engine, rules and replay contributors"
maintenance: "hand-maintained"
adr_id: "0162"
decision_status: "accepted"
date: "2026-11-08"
---

# ADR 0162: Mandatory linked loop draws

## Context

A mandatory entry exile and its separate departure return can repeat across
fresh permanent incarnations. Exact record hashes change on each transition
because they retain those incarnations, turn history, and command audit data.
CR 104.4b and 732.4 require a draw once the actions in the loop are forced.
CR 732.5 leaves unrelated priority responses up to their players.

## Decision

The trigger-target owner observes the position when one trusted entry exile
has one required legal target. It checks the linked ability pairs and passive
combat keywords in scope and retains rule-relevant state and current link
relationships in the comparison. Known generation and audit fields can
advance within the same position. Repeated arrival, departure and targeting
facts can accumulate in that position when participating effects and eligible
actions have no history-dependent availability. The actual record retains all
of those facts.

Completed decisions outside the forced target and priority passes discard the
witness. The witness stores one earlier position and advances its sampling
interval with Brent's recurrence test. The state-based execution owner commits
the terminal draw and invalidates the current action capability. The existing
simultaneous-loss path delegates to the same terminal mutation and event.

## Alternatives

A repetition limit could end a game that still has a legal choice. Matching
only public names and zones could conflate different links, resources or
active rules. A loop shortcut negotiated by players has a separate decision
contract under CR 732.2.

## Consequences

The proof covers recurrent mandatory linked exile and return in the closed
automatic scope. Other automatic effects or a different legal target leave
their ordinary decisions in place. New games retain versioned recurrence
evidence across save and replay. Historical records that omit the field retain
their earlier behavior.
