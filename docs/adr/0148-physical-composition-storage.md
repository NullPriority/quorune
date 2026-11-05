---
title: "ADR 0148: physical composition storage"
status: "ADR"
authoritative_source: "Physical topology, canonical card codec and zone mutation ownership"
verified: "2026-10-31"
audience: "rules, persistence and replay contributors"
maintenance: "hand-maintained"
adr_id: "0148"
decision_status: "accepted"
date: "2026-10-31"
---

# ADR 0148: physical composition storage

## Context

CR 730 retains one battlefield object while several cards or tokens represent
it. Top characteristics, constituent abilities, physical commander identity and
the eventual destinations of all parts have distinct rules. Treating every
part as a live permanent would change targeting, counts, state actions and event
discovery. Discarding the incorporated objects would lose physical cards when
the permanent later leaves.

## Decision

Keep one live carrier and an immutable `PermanentCompositionStorage` on its
optional `permanent_composition` field. The store couples the retained topology
to canonical physical/copiable snapshots and ordered merge timestamps. Part
identities and references remain unique across live objects and other stores.
Physical snapshots cannot recursively contain another live store.

The card codec omits absence and validates participating carriers through the
closed schema-one value. A snapshot can reconstruct fresh detached card data;
it cannot insert that data into a game. The canonical zone owner commits
physical incorporation through the existing transaction and stack-departure
cleanup. Casting and effect owners remain responsible for permission.

Current-object registration and detachment share `GameObjectStore`. Its in-place
update/pop port replaces the existing copy-object and emblem card-map assignment
identities; token registration uses the same port. The structural identity and
total direct-write ceilings stay unchanged, and the engine and emblem ceilings
each decrease by one. Stack cleanup moves from the zone commit method to the
shared departure method without adding a write site. The exact transfer is bound
to this ADR in the architecture exception registry; size and other allowances
remain unchanged.

Physical incorporation and complete rules execution have separate readiness
boundaries. The physical kernel preserves the retained object and archives the
stack part. Mutate admission also requires composed characteristics and abilities,
face handling and complete departures. Further incorporation and zone movement
reject before commitment where those owners remain unrepresented.

The copiable characteristic planner combines top-component values with all
constituent ability fragments and activation catalogs. Its merge effect enters
the existing layer-1a evaluator at the recorded merge timestamp. Closed copied
source/face fragments retain triggered-program lookup and multiplicity through
the existing semantic registry. Physical storage remains separate from those
copiable values. Repeated incorporation uses the same preparation and transaction
owners. Copied static-component occurrences carry creation time separately from
their applicability keys, rebase at layer-1a application and lower to distinct
effects through the existing continuous owner. Copied candidates use a pure
layer-one read through that same owner, then participate at their own source
layer through the existing dependency and applicability queries. Ephemeral copy
tracking survives catalog replacement without entering saved characteristic
snapshots. Face and departure readiness remain distinct.

## Alternatives

Keeping all parts in battlefield object collections manufactures extra
permanents. Storing only the top card loses underlying cards, faces and commander
designations. Copying the current effective object into each part conflates
copiable values with controller, counters, damage and transient effects. An
untyped annotation or separate mutation registry duplicates schema or ownership
authority.

## Consequences

Ordinary card payloads retain their existing shape. Participating records retain
enough physical data for a complete group departure while exposing one current
object to normal battlefield membership. Stale preparation and failures during
commitment roll back through the existing transaction boundary.

Departure ordering, replacement coupling, linked references, token cleanup and
commander exceptions have separate readiness boundaries. A complete departure
publishes one permanent departure and the appropriate physical card arrivals.
No card gains follow from storage readiness alone.

The physical split kernel accepts a validated order from a trusted caller and
commits through existing reset and placement ports. Additive occurrence roles
separate parent departure from physical arrivals, so card-arrival history and
permanent death counts remain distinct. The kernel supplies no ordering UI or
authorization to bypass unresolved replacement and commander choices; ordinary
movement guards stay until the complete continuation is represented.

A single resolving departure uses the existing semantic-choice envelope
for private physical ordering. The completion retries its original trusted
instruction under scoped immutable order data, preserving the current resolution
cursor and permission owners. The stored root is unchanged until completion.
Represented whole-object destination replacements are selected once through the
existing replacement owner. Their fixed counter leaves project onto each new
physical arrival before event publication. The source's placing player and the
destination's ordering principal remain separate from the original resolving
actor. Plain ordering retains its schema-one wire shape; resolved destination
and ordering-actor data use schema two. Nested counter modifiers, permissions,
multi-object, cost, state-based and commander compositions retain their separate
readiness boundaries.
