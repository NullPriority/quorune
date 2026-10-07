---
title: "ADR 0106: typed public entry designations"
status: "ADR"
authoritative_source: "entry designation compiler, replacement journal, card-designation intent and fixed-query continuous-effect owners"
verified: "2026-10-07"
audience: "rules, compiler, replay, privacy and architecture contributors"
maintenance: "hand-maintained"
adr_id: "0106"
decision_status: "accepted"
date: "2026-10-07"
---

# ADR 0106: typed public entry designations

## Context

An intrinsic choice made as a permanent enters belongs to its entry replacement
transaction, rather than an enters trigger. CR 614.1c and 614.12a require the
choice before entry. The entering controller chooses, even when another player
owns the card. Colors and creature types have closed rules vocabularies under
CR 105.1 and 205.3m. A later zone object loses the choice under CR 400.7, and a
copy must make its own entry choice under CR 707.2.

## Decision

Compile a mandatory intrinsic choice of one color or one creature type into
`replacement.zone.entry-designation.v1`. The descriptor contains the choice
kind, source span, current static-component identity, and capability closure.
The replacement owner derives the five colors or pinned creature-type
vocabulary; runtime code does not read Oracle prose.

Use the existing public replacement journal and `SetField` operation. Each
legal value is one mutually exclusive candidate for the original entering
object. The selected field prevents another value from applying, and the
existing continuation retains the choice, actor, ordering and original
incarnation. These are ordinary entry replacements, rather than self
replacements as defined by CR 614.15.

Carry the selected value in `PreparedZoneChange`. Cards and copied tokens
commit it through the existing `SetCardDesignationIntent` owner before their
entry facts are published. Add `chosen_color` to that closed intent vocabulary;
reuse `chosen_creature_type`. Existing zone-object reset removes these values
when the object departs. Copiable snapshots do not retain the choice.

Compile fixed public creature-set characteristic bonuses referring to the
chosen value into `continuous.characteristics.chosen-designation.v1`. Its
closed child descriptor uses the existing fixed-query anthem, keyword or
combined characteristic owner. Lowering adds only the source's retained color
or subtype to the typed predicate. Controller and opponent relations remain
owned by the existing query, and current static-component participation gates
both the entry and characteristic descriptors.

## Evidence and boundaries

Original printed cards prove strict whole-program closure, an offered and
accepted cast, pre-entry selection, principal-correct rejection, hidden-card
projection, session save/load and exact command replay. A printed copy spell
proves a fresh choice for its copied permanent, unchanged source choice,
stale-choice rejection, state-based consequences and exact replay. Separate
owner evidence checks vocabulary, malformed descriptors, mutually exclusive
choices, public query scope and zone-object reset. Removing either compiler
production or blocking the designation capability prevents exact closure.

Chosen-value mana, trigger queries, costs, targets, dynamic characteristics,
restricted or optional choices, multiple same-kind linked choices and
independently unsupported sibling abilities remain outside this grammar.
An entry choice linked to making the source the chosen type also remains
outside this owner. Its source-spanned entry node stays residual, so an
existing reviewed program retains one choice and its linked type addition.
Recognizing an entry clause does not admit its incomplete parent card.

Existing records without these descriptors retain their earlier choices,
execution and event payloads. New records pin the component and capability
inventories. This adds no universal semantic operation or new mutation path.

## Alternatives

An enters trigger would make the choice after entry and allow intervening
priority. A separate selection transaction would duplicate the replacement
journal's ordering, persistence and principal validation. Compiling each
possible chosen value into a different card program would require runtime
program replacement and weaken source identity. The existing entry journal
and designation intent retain one source-bound compiled program.

## Consequences

The family reuses entry ordering, principal capabilities, rollback, static
participation, target queries and replay. Future chosen-value uses must extend
their own typed owners and produce new complete-card evidence; they cannot
inherit support from the entry choice alone. Generated cohort and corpus
reports own changing card counts and eligibility measurements.
