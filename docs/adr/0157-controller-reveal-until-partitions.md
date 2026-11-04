---
title: "ADR 0157: controller reveal-until partitions"
status: "ADR"
authoritative_source: "Typed library prefix, fixed selection and canonical movement owners"
verified: "2026-11-02"
audience: "rules, compiler, privacy and replay contributors"
maintenance: "hand-maintained"
adr_id: "0157"
decision_status: "accepted"
date: "2026-11-02"
---

# ADR 0157: controller reveal-until partitions

## Context

A reveal-until instruction inspects consecutive library cards through the
required qualifying match. CR 701.20b leaves revealed cards in their zone until
the instruction moves them. CR 608.2c and 609.3 preserve the complete printed
procedure when the library has no match or fewer matches than requested.

The existing fixed library-selection owner already validates identity-pinned
hand and battlefield selections with exhaustive graveyard or bottom remainders.
Discover instead exiles cards one at a time and retains casting permissions;
those semantics do not describe a reveal that leaves cards in the library.

## Decision

A closed schema-one instruction represents the resolving controller, a fixed
positive match count, current characteristic predicate and complete result.
A pure query scans top first and stops immediately at the required match.
Cards remain in the library during that scan. It reads no later card's
characteristics and never asks a player to select the scan length or stop set.

Matching-card results delegate to the existing fixed-selection instruction
with the actual prefix length and mandatory matching policy. Battlefield
results retain their existing entry and tapped-state dependencies. The fixed
choice owner continues to pin physical/logical identities and revalidate the
current top before committing the exhaustive partition.

An all-revealed-to-hand result has no player choice. It directly prepares the
existing public reveal and library-selection intents for the exact prefix.
This uses the same transaction and mutation owners without changing historical
fixed-selection choices. Empty libraries perform no inspection or movement.
Too few matches use the entire available prefix and retain every applicable
printed result; this procedure does not draw cards.

New descriptor inference requires the prefix and fixed-selection capabilities
independently of program coverage annotations. Entry forms additionally infer
their battlefield and tapped-state requirements. Existing activated admission
recognizes this capability-shaped instruction through the same dependency gate.
Unsupported sibling text cannot promote a partial body.

Random bottom order retains the independent positional-knowledge correction in
[ADR 0156](0156-versioned-library-order-knowledge.md). It does not become a shuffle.
Public inspection does not authorize disclosure of a private resulting order.
The bridge adds no state mode, continuation kind, component registry, mutation
owner or runtime Oracle parser. Its explicit descriptors remain source-pinned
through ordinary semantic and record provenance.

## Alternatives

Reusing Discover would move cards too early and create unrelated exile/cast
semantics. A dynamic count selected by a client would transfer rules authority.
A second movement implementation would duplicate identity and replacement
handling. Changing historical fixed-selection auto-completion would alter old
command streams. Treating recognition as whole-card support would hide missing
costs, triggers, companion instructions or runtime dependencies.

## Consequences

Spells, activated abilities and triggers can share the closed controller scan
while retaining their ordinary costs, timing and source-controller authority.
No-match, short-library, public disclosure, tapped entry, invalid partitions,
pending choices and replay remain testable at their owning boundaries.
Other players' libraries, chosen or variable stopping conditions, linked
revealed-count or mana-value consumers, casting results, shuffle-the-rest
forms and unrepresented destinations remain explicit boundaries.
