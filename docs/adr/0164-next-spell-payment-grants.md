---
title: "ADR 0164: Next spell payment grants"
status: "ADR"
authoritative_source: "CR 514.2, 601.2i, 611.2a, 702.51 and 702.126 and the canonical cast payment owner"
verified: "2026-11-09"
audience: "compiler, rules, casting and replay contributors"
maintenance: "hand-maintained"
adr_id: "0164"
decision_status: "accepted"
date: "2026-11-09"
---

# ADR 0164: Next spell payment grants

## Context

A resolving ability can grant Convoke or Improvise to the controller's next
spell during the current turn. The grant must survive the source's departure,
be available while the player pays for the spell, and expire if the player
reaches cleanup without casting. Existing records store an older Improvise
flag and rely on its exact serialized behavior.

## Decision

Compile one closed `grant_next_spell_payment` result in the existing state
and permissions owner. The resolved controller receives an immutable window
keyed to the current turn sequence. It records a canonical set of the granted
payment kinds. The effect's capability binding includes its specific payment
owner. Repeated grants of one kind are redundant.

The shared cast query uses the window when forming payment options. Final cast
completion consumes it after payment. A rejected cast restores it through the
existing transaction. The turn owner clears the window during cleanup. Old
Improvise records retain their original field and consumption behavior. A
source-equivalent reviewed activation can be retired when the typed catalog,
price, and result all close.

When a current typed grant supplies a payment ability, one cast can combine
Convoke and Improvise. The existing contribution search includes artifact-only
candidates as generic payment resources. The shared cast query confirms a
disjoint selection, and the post-mana owner rechecks the actual chosen
creatures and artifacts. Historical payment contexts retain their earlier
availability rules.

## Alternatives

An object annotation would require a spell object when the granting ability
resolves. A player window keeps ownership on the resolving controller and
lets the casting owner determine which spell receives it. Reusing the older
Improvise flag for both keywords would leave the granted kind and turn absent
from its serialized contract.

## Consequences

The only operation inventory allowance is `grant_next_spell_payment`.
Controller, kind, version, and duration are checked before the canonical
mutation. The new descriptor and window are serialized only when used.
Gameplay authority remains with the existing cast, tap, mana, and cleanup
owners. This result supplies no alternate casting permission.

## Removal condition

A successor must preserve controller identity, redundancy, successful-cast
consumption, expiry, rollback, and exact replay of both window formats.
