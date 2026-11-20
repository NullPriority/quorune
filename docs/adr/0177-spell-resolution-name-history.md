---
title: "ADR 0177: Spell resolution name history"
status: "ADR"
authoritative_source: "CR 201.2a, 608.2b, 608.2m and 702.192a; pinned Oracle/rulings"
verified: "2026-11-17"
audience: "rules, resolution, copy, replay and multiplayer contributors"
maintenance: "hand-maintained"
adr_id: "0177"
decision_status: "accepted"
date: "2026-11-17"
---

# ADR 0177: Spell resolution name history

## Context

A first-resolution condition requires information retained for the entire game.
Current zones and cast counts cannot establish whether a controller has already
resolved a spell sharing a name. Copies can resolve without being cast, and an
all-targets-illegal spell does not resolve. An unnamed object shares no name;
objects with multiple names share a name when any name overlaps.

Resolution can pause for choices or replacements. Recording the same stack
object again would change its first-resolution result during that pause.

## Decision

Use immutable `SpellResolutionNameHistory`, `SpellResolutionNameFact` and
`SpellResolutionNameLedger` values. The ledger retains names per controller and
holds a transient fact for each active stack reference. Reentering an active
resolution returns that same fact; finishing releases the transient fact while
retaining the lifetime names.

Add one nullable, versioned ledger field to `GameState`. New games initialize
known-empty history. Missing historical state remains unavailable and is omitted
when serialized again. Record-format provenance must agree with the initial
checkpoint. A new first-resolution condition cannot treat unavailable history as
an empty history.

Capture current rules names and controller after target legality succeeds and
before executing effects. Include ordinary spells, permanent spells and spell
copies. Keep names distinct from display labels and preserve nameless face-down
spells. Release the active claim only after successful resolution completion.
Canonical transactions restore both the ledger and active facts on a fault.

One narrow commit function owns assignment of the immutable ledger. The reviewed allowance
adds exactly that structural write identity and its owner declaration. It
must preserve every unrelated architecture allowance, operation, engine-method
ceiling and size ceiling. It must not rebuild the baseline from the full moving
development checkout. No arbitrary statistics store or second history registry
supplies this authority.

## Alternatives

Counting casts excludes resolving copies and includes countered spells. Scanning
zones loses earlier objects. Tracking only Paradigm resolutions misses an earlier
spell sharing the name. Recomputing the first flag during a pause counts the
active spell against itself. Substituting empty history for an old checkpoint
asserts facts that the record does not contain.

## Consequences

Focused native evidence covers current names, selected and fused faces, uncast
copies, unnamed spells and copies, target failure, independent controllers,
resolution pauses, rollback, completion cleanup, historical absence and exact
replay. The checkpoint codec rejects pending claims without their original active
spell reference and controller. The existing mutation-owner and source-policy guards remain required.
The immutable model tests alone do not certify live integration or card support.

## Removal condition

A successor preserves complete controller-scoped lifetime facts, stable active
claims, truthful legacy absence, transactional rollback and exact replay through
one typed owner.
