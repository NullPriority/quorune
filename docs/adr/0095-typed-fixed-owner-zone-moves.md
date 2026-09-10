---
title: "ADR 0095: typed fixed owner-zone moves"
status: "ADR"
authoritative_source: "fixed owner-zone compiler grammar, target and attachment references, public choices, and canonical zone transitions"
verified: "2026-09-10"
audience: "rules, compiler, zone, replay, and architecture maintainers"
maintenance: "hand-maintained"
adr_id: "0095"
decision_status: "accepted"
date: "2026-09-10"
---

# ADR 0095: typed fixed owner-zone moves

## Context

Many Oracle instructions move one public object to a zone belonging to that
object's owner. The object may be a target, the resolving source, the source's
current or last-known enchanted creature, one controller-selected permanent,
or one permanent selected by each player. These forms share target
revalidation, logical incarnation, attachment references, APNAP choice,
Commander replacement, hidden-destination projection, and replay concerns.

The engine already owns the required scalar `move`, library shuffle, and
simultaneous public-choice operations. A second zone operation or a generic
runtime result-binding language would duplicate those authorities.

## Decision

Compile only the closed fixed owner-destination grammar into one immutable
`FixedOwnerZoneMoveTemplate` and one shape-gated capability. The template
selects an existing target, source, attachment, or public-choice reference and
emits only the registered `move`, `shuffle_into_library`, or
`choose_cards_apnap` operation. The canonical zone-transition owner derives
the destination container from physical ownership and preserves current
replacement, trigger, privacy, and logical-incarnation behavior.

Targeted library movement accepts only top, bottom, second, third, or fourth
position. Graveyard targets are public cards, exile targets must be face up,
and battlefield targets use a closed type or characteristic-form predicate.
The target schema therefore gains one generic `face_down` object fact; offer,
command, and resolution checks consume it through the existing target owner.

The existing simultaneous semantic intent was already serializable and
validated but absent from the replacement replay decoder's closed kind list.
Admit `move_objects_simultaneously` to that existing decoder so a suspended
four-player owner return replays without creating a second continuation type.

## Alternatives

- Add owner-relative move operations per wording or destination. Rejected
  because the existing effect and zone owners already express the mutations.
- Resolve attachment text or card names at runtime. Rejected because compiled
  typed references, not Oracle prose or identity, own behavior.
- Add an open library-position or public-object query. Rejected because this
  harvest has evidence only for the enumerated positions and predicates.
- Treat every owner-zone clause as a target. Rejected because source,
  attachment, and public selection have distinct rules and lifecycle meaning.

## Consequences

Spell, triggered, activated, and modal programs can share one compiler family
without another runtime parser, zone engine, operation, or card-specific
branch. Targeted forms remain sound and complete within their declared public
scope; source and attachment forms remain incarnation-aware; each-player
choices complete in APNAP order before one replacement-aware batch; and hand
or library results retain principal-correct projection and exact replay.

Heterogeneous spell-or-permanent targets, hidden selection, broad mass moves,
controller destinations, alternative costs, delayed or linked results,
arbitrary library positions, variable counts, unsupported attachment
relations, copied or text-changed abilities, and independently unsupported
siblings remain fail-closed residuals. This family adds no family-specific
ability-presence query and performs no dynamic characteristic count.

## Removal condition

Retire this compiler family only if a successor preserves its closed source
spans and descriptor validation, target soundness and completeness, physical
ownership, logical incarnation, attachment LKI, APNAP selection, Commander and
other destination replacements, hidden-destination projection, rollback, and
exact replay without runtime prose, card identity, or duplicate mutation
authority.
