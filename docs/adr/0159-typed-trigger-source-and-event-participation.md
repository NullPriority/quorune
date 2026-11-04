---
title: "ADR 0159: typed trigger source and event participation"
status: "ADR"
authoritative_source: "Trigger participation model, compiler and discovery owners"
verified: "2026-11-02"
audience: "rules, compiler, replay and multiplayer contributors"
maintenance: "hand-maintained"
adr_id: "0159"
decision_status: "accepted"
date: "2026-11-02"
---

# ADR 0159: typed trigger source and event participation

## Context

CR 603.2d makes additional-trigger effects produce independent triggered
abilities. Their counts add without recursively multiplying each other. A
prohibition prevents triggering before any use limit is consumed. Printed
predicates distinguish the triggering ability's source from the causal object
or action. Entry and look-back departures also use different source views.

The existing participation owner represents artifact-or-creature entry and a
chosen-type creature source. Extending those promises requires explicit data
and event-time matching, rather than runtime Oracle interpretation or another
trigger registry.

## Decision

Keep the historical schema-one fragment and handler unchanged. Schema two
contains exactly its schema, typed predicate, closed rule and optional canonical
creature subtype. The pure domain catalog constructs existing immutable object
queries; a rule-layer reader evaluates source and event facts. It does not
accept arbitrary text or executable predicates.

Separate new registered handlers represent additional instances and
prohibitions. Each requires its own capability, and the persisted fragment
independently infers that requirement. A wrong outcome, unknown rule/subtype,
legacy/new format mismatch or unowned companion clause rejects.

Discovery uses the same pre-event source and characteristic snapshots as the
normalized event owner for look-back departures. Entry uses the current
post-event battlefield. Source controller, type, subtype, color, legendary
status, self exclusion and attachment conditions remain distinct from causal
entry, death, attack, combat damage or cast/copy facts. Equipment conditions
read the matching event-time equipment view and ignore phased attachments.

Prohibitions run before ordinary, Evoke, Blitz and granted Prowess collection
and before quota consumption. Additional instances use the existing pending
batch and ordinary APNAP/target selection; no spell-copy event is emitted.
Multiple modifier sources add independently. Existing once-per-turn limits
cap the actual resulting instances. Ordinary stack copies retain their own
copy semantics.

No second component registry, mutation owner, APNAP coordinator or global state
field is added. Old descriptors and source contexts retain their prior shape.
New typed rules use explicit fragment data and ordinary declared runtime
provenance. This extends [ADR 0058](0058-typed-trigger-processing-owner.md).

## Alternatives

Matching every qualifier against the causal object confuses source-subtype
effects with entering-object effects. Reading only the current battlefield
loses simultaneously departing modifiers or prohibitions. Copying an already
targeted stack ability prevents independent choices. Applying quotas before
prohibitions spends an allowance for an ability that never triggered. A free
form runtime predicate or another registry would duplicate rules authority.

## Consequences

The same owner supports bounded source/event predicates and prohibitions while
retaining ordinary choices, hidden-information projection, rollback and replay.
Dungeon-room behavior, unowned pre-game choices and unsupported companion
instructions remain material residuals. Recognition of a static clause does
not establish complete card admission or native evidence for every card.
