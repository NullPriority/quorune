---
title: "ADR 0153: merged commander damage identities"
status: "ADR"
authoritative_source: "Physical commander identity, immutable damage source and final damage transaction owners"
verified: "2026-11-01"
audience: "rules, persistence and replay contributors"
maintenance: "hand-maintained"
adr_id: "0153"
decision_status: "accepted"
date: "2026-11-01"
---

# ADR 0153: merged commander damage identities

## Context

CR 903.3c makes a merged permanent containing a commander that player's
commander. Its retained live root can be a different physical card. The root's
scalar designation therefore cannot identify every commander responsible for
combat damage under CR 903.10a. Copy effects, current characteristics and control
changes do not transfer a physical designation.

## Decision

Capture a closed `PhysicalCommanderDamageIdentities` value from the validated
physical composition when constructing a damage source snapshot. Each entry
contains its physical designation and Oracle identity. The set is nonempty,
canonical and unique by designation. A contradictory scalar designation or a
noncommander source carrying the set rejects.

Transport the immutable value through the existing damage proposal, replacement
event and final result. The damage transaction maps its final combat damage to
each applicable commander ledger while applying ordinary damage results once.
It does not consult the current source object again to reconstruct identities.
Public ledger lookup reads an archived physical commander without exposing its
zone. Physical source facts stay separate from copied characteristics and the
retained root's own designation.

Ordinary sources omit the new field and keep their scalar behavior and wire
shape. The closed nested schema is additive; unknown or malformed contents
reject. Historical commander identity-version absence keeps its Oracle-key
mapping, and declared runtime provenance retains its existing compatibility
boundary. No designation is invented for a noncommander card and no new damage
mutation owner is introduced.

## Alternatives

Assigning one component's designation to the root conflates distinct physical
cards and loses other commanders. Looking up the live object during commitment
loses source facts after departure. Applying damage separately per commander
would multiply life loss and other ordinary damage consequences.

## Consequences

Native offered combat validates the retained-root distinction, ownership,
pending load and replay. Typed source/result controls cover multiple identities,
noncombat exclusion, departure snapshots, malformed inputs and ordinary absence.
This foundation grants no Mutate casting permission or original-card admission.
