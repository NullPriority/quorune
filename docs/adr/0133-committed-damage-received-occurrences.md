---
title: "ADR 0133: committed damage received occurrences"
status: "ADR"
authoritative_source: "received damage model and canonical damage transaction"
verified: "2026-10-27"
audience: "damage, compiler, trigger and replay contributors"
maintenance: "hand-maintained"
adr_id: "0133"
decision_status: "accepted"
date: "2026-10-27"
---

# ADR 0133: committed damage received occurrences

## Context

The canonical damage transaction retains one result per dealer and recipient.
A receiving ability observes the simultaneous damage received by its object,
even when several dealers contribute. Reusing dealer packets for that ability
creates duplicate triggers and exposes individual packet amounts instead of
the actual received total. Outgoing subscriptions and saved packet-based
programs already depend on the existing stream.

## Decision

Add an immutable `ReceivedDamageOccurrence` derived from positive committed
results, grouped by recipient physical and logical incarnation within one
transaction. Canonical recipient order is independent of proposal order.
Keep the total actual damage and its actual combat portion separate.

The existing damage collector publishes `damage.received` for the total and
`combat.damage.received` for a positive combat portion. Each stream supplies
its own correct `amount`. Zero or fully prevented packets produce no receiving
occurrence. Dealer packets remain unchanged; no persistent grouping latch or
second damage mutation owner is introduced.

Newly compiled receiving subscriptions use these events and require
`trigger.event.normalized_received_damage`. Runtime inference derives that
minimum from subscriptions even without coverage annotations. Source identity,
queued effects after lethal damage, current/LKI rules, ordinary trigger
placement and targeting retain their existing owners.

## Alternatives

Deduplicating raw dealer packets loses source semantics and cannot supply the
received total. Reinterpreting persisted `damage.dealt` changes historical
program meaning and replay. A permanent pending latch merges separate hits or
damage steps and adds unnecessary authoritative state.

## Consequences

Simultaneous sources create one receiving occurrence per recipient, while
separate transactions and first/double-strike steps remain separate. Actual
replacement and prevention results determine the amounts and final recipients.
The trigger controller belongs to the observing ability, not an arbitrarily
chosen dealer. Legacy serialized packet subscriptions retain their original
event and replay behavior. Existing outgoing and per-player batch aggregation
remain separately testable. Unrepresented body grammar, linked results and
recipient predicates remain compiler residuals; a corrected transport alone
does not certify those cards.
