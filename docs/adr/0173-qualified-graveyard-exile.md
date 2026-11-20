---
title: "ADR 0173: Qualified graveyard exile"
status: "ADR"
authoritative_source: "CR 108.2, 110.1, 111.8, 614, 616 and 730.3e"
verified: "2026-11-16"
audience: "compiler, replacement, zone and replay contributors"
maintenance: "hand-maintained"
adr_id: "0173"
decision_status: "accepted"
date: "2026-11-16"
---

# ADR 0173: Qualified graveyard exile

## Context

Static graveyard redirection can select cards, cards or tokens, battlefield
permanents, types or opponent relations. Owner and controller differ for
borrowed objects. Current characteristics before movement determine membership.
Card-only redirection also has a distinct physical-component meaning when a
merged permanent is a token; an explicit nontoken-permanent clause does not.

## Decision

Add one explicit handler identity with a closed version-one
`GraveyardExileSpec` in the existing zone replacement registry. Delegate all
destination mutation, ordering, costs, event publication, continuation and
rollback to the canonical zone replacement owner. Read existing immutable
pre-move facts instead of card identity or runtime Oracle text.

A versioned derived-qualification lookup classifies whole card/token and
nontoken creature subjects from existing zone-event fields. It adds no stored
payload fields. New qualifications reject malformed or spoofed identities.
The owner relation uses captured ownership; a battlefield controller relation
uses the affected object's canonical chooser, which is its controller there.

Keep card-only conditions for the existing composition adapter. Use explicit
whole-permanent qualifications for the nontoken-creature battlefield clause,
including its off-battlefield creature-card alternative. The counter-bearing
opponent-card handler, old descriptor fields, old context/dataclass layouts and
historical event payloads remain unchanged. The new compiler production requires
current admitted ability participation, so source removal and ability loss keep
their existing rules authority.

## Alternatives

- Widening the old unmarked handler would reinterpret historical inputs.
- Treating every noncard as a token would include spell and card copies.
- Checking printed types after movement would misclassify animated lands,
  face-down creatures and bestow Auras.
- Projecting every creature-card condition onto physical components would
  violate an explicit nontoken-permanent exclusion.
- Adding a second chooser or zone mutation path would split authority.

## Consequences

Death triggers depend on the final destination; discard and sacrifice results
retain their own action facts. The source can apply to its own simultaneous
departure. Additional token results and linked exile-card permissions remain
outside this closed descriptor until separately represented.
