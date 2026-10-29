---
title: "ADR 0125: layer-six source participation"
status: "ADR"
authoritative_source: "continuous effect ordering and characteristic evaluation host"
verified: "2026-10-26"
audience: "rules, compiler, replay and architecture contributors"
maintenance: "hand-maintained"
adr_id: "0125"
decision_status: "superseded"
date: "2026-10-26"
---

# ADR 0125: layer-six source participation

Superseded by [ADR 0139](0139-static-source-removal-dependencies.md), which
makes source-removal dependencies explicit before evaluating the ordered prefix.

## Context

The current component snapshot correctly describes which abilities remain
after layer six. It cannot alone decide whether a printed static effect
already started before its source lost that ability. CR 613.6 preserves an
effect that began in an earlier layer; dependency and timestamp order also
distinguish earlier and later ability removal within layer six.

The pinned Toxicrene ruling exposes this distinction when Ashaya makes both
creatures lands. Toxicrene's replacement ability must remain applied after it
removes its own printed abilities. An earlier removal prevents it from
starting; a later removal may erase the source's abilities without undoing
effects already applied to other objects.

## Decision

Keep one canonical effect order and one characteristic evaluator. The pure
collector can return internal effect-to-source-program metadata alongside
candidate effects. The host evaluates a printed layer-six source's component
presence through the ordered prefix before that program's first effect.
Earlier candidates from an already inapplicable source are excluded through
the same participation query. Prefixes decrease in canonical order.

Use those filtered candidates for the final component snapshot as well as
source selection. Preserve the existing earlier-layer query for programs that
start before layer six. Programs with no represented layer-six emission retain
the established current-snapshot path.

When ability-removal operations are present, collect the additional layer-six
effects needed to preserve ordering for ordinary keyword givers. Otherwise
retain conservative presence-only collection. Completed plans and source reads
are memoized only within one synchronous pure read frame. They never survive
an authoritative mutation, and no mutable state revision cache is introduced.

Composite quoted grants reuse the existing layer emitter. Ability removal and
replacement fragments belong to one effect; independently compiled children
retain distinct identities and costs. Land subtype removal and basic-type
addition retain their separate layer-four operations. Removing a represented
star definition cannot prevent later numeric modifiers from applying to the
rules-defined undefined value.

## Alternatives

- End-of-layer presence alone drops effects that already started.
- Before-layer presence alone admits sources removed by earlier effects.
- A card-specific exception or duplicate layer evaluator gives separate
  authority to syntax, identity or a special case.
- A long-lived result cache can retain stale control, ability or timestamp
  state across commands.

## Consequences

Native witnesses distinguish earlier and later removal, global keyword givers,
the actual Ashaya/Toxicrene interaction, replacement mana, independent costs,
source departure, token and controller scope, private projection and replay.
The source plan adds no effect operation, GameState field or serialized cache.
Historical single-quote identifiers and land-only addition codecs remain
unchanged; new composite slots and creature-land scopes are explicit.

## Removal condition

Retire the prefix query only when a shared source lifecycle owner provides the
same per-layer participation, ordering, scope and replay guarantees without
reintroducing an end-snapshot or card-identity exception.
