---
title: "ADR 0154: physical commander destination replacement"
status: "ADR"
authoritative_source: "Commander replacement, immutable zone snapshots and physical departure owners"
verified: "2026-11-01"
audience: "rules, persistence and replay contributors"
maintenance: "hand-maintained"
adr_id: "0154"
decision_status: "accepted"
date: "2026-11-01"
---

# ADR 0154: physical commander destination replacement

## Context

CR 903.9b-c replaces a physical commander's hand/library movement before it
happens. In a merged permanent, accepting that replacement sends only commander
components to command; the other cards continue to the appropriate ordinary
destination. A single destination on the retained root cannot represent this.
The root can be a noncommander physical card, and its controller need not own
the commander choice.

## Decision

Capture closed `CommanderZoneComponent` values from the validated store in the
existing zone-change subject snapshot. Each value identifies its physical
card, incarnation, public reference, designation and owner. The existing
replacement registry generates owner-optional commander effects. A constrained
union operation records only known designation identities in the event's
`commander_component_returns` field; it cannot redirect an arbitrary card.

Retain the base permanent destination and the selected physical exceptions.
The existing split transaction restores each card and applies its represented
destination through canonical reset and placement ports. Schema-three ordering
requests retain those component destinations, exclude commanded cards from
library permutations and rebuild the complete physical commitment order.
No part moves while replacement or required ordering is pending. Hand movement
does not create a rules-irrelevant ordering decision.

Composed destination changes advance a nonnegative event epoch. Intrinsic
commander effects are generated for the bounded number of represented
destination-changing operations, allowing the rule to be offered after a later
replacement changes that same event. Ordinary scalar commander replacements and
uncomposed event fields keep their existing representation.

Unknown components, stale stores, invalid permutations and unresolved choices
reject before commitment. Mixed-owner/controller competing replacement scopes,
unrepresented per-destination counter riders, mixed-token/card-only replacement,
linked returns, costs and player-departure continuations retain their separate
boundaries. No new replacement registry or zone mutation owner is introduced.

## Alternatives

Changing the whole root's destination sends noncommander cards to command.
Assigning an arbitrary designation to the root loses physical identity. Moving
the commander before other choices exposes a partial transition. A separate
commander movement transaction would duplicate replacement and zone authority.

## Consequences

Actual Unsummon and Griptide witnesses cover accepted and declined owner
choices, split destinations, private library ordering, pending load and replay.
Typed controls cover closed source data and reapplication after destination
changes. This foundation grants no Mutate casting permission or original-card
admission.
