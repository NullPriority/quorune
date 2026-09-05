---
title: "ADR 0086: typed fixed controlled characteristic effects"
status: "ADR"
authoritative_source: "fixed controlled characteristic compiler and continuous-effect runtime"
verified: "2026-09-05"
audience: "rules, compiler, continuous-effect, replay, and architecture maintainers"
maintenance: "hand-maintained"
adr_id: "0086"
decision_status: "accepted"
date: "2026-08-27"
---

# ADR 0086: typed fixed controlled characteristic effects

## Context

Oracle instructions often grant fixed power/toughness, a fixed keyword, or both
to a controlled or public battlefield set until end of turn. Public sets include
all creatures, creatures controlled by opponents or one target player, current
attacking or blocking creatures, and attacking creatures other than the source.
Static fixed-query components already own the closed set grammar and keyword
consumer capabilities, while resolution-created effects already own
logical-object locking and cleanup. Treating the duration-qualified text as a
live static set would incorrectly include later entrants and stop following
affected objects after control or source changes.

## Decision

Reuse the static fixed-query grammar only to derive a closed typed predicate and
supported keyword set. A single query validator also accepts fixed controller
exclusion, one revalidated target-player controller, current attacking or
blocking state, and source-reference exclusion for other attacking creatures.
During resolution, evaluate that predicate against the canonical
effective-characteristic and combat-relationship boundaries, including
represented layer-4 and layer-5 type and color changes, then lock the matching
physical and logical object identities. Do not evaluate dynamic characteristic
counts, power, toughness, or ability predicates.

Commit supported ability additions through the shared layer-6 evaluator and
fixed power/toughness changes through layer 7c over the identical locked set.
Both layers use the existing until-end-of-turn journal and cleanup owner. One
canonical keyword-to-consumer-capability map is shared by the compiler, static
semantic validator, resolution capability shape, and runtime validator. The
older power/toughness-only template retains its semantic identity.

## Alternatives

- Register a duration-specific static component. Rejected because a live set
  would violate resolution-time membership and logical-object locking.
- Add keywords directly to card annotations. Rejected because additions and
  removals must share layer-6 ordering and applicability.
- Reparse Oracle text during resolution. Rejected because authoritative
  behavior and replay must consume typed program data only.
- Admit dynamic counts with the fixed predicate grammar. Rejected because
  dynamic characteristic dependencies require a separate cycle-safe owner.

## Consequences

The family composes across spell, triggered, activated, loyalty, and modal
carriers while retaining target revalidation, multiplayer opponent scope,
current combat-state membership, source departure, control change, later-entry,
zone-change, cleanup, privacy, rollback, and exact-replay behavior. Existing
type and color changes may determine the resolution-time set, but the new
effect does not itself change type or color.

Multiple or opponent-only targets, attachment-relative, tapped, modified,
keyword-qualified, counter-qualified, dynamic, chosen, conditional, quoted,
Protection, unsupported keyword, type-changing, and variable-duration forms
remain fail-closed. New keywords must extend the canonical consumer map and
prove their actual rules consumer before entering this boundary.

## Removal condition

Retire this owner only if a successor preserves the same closed grammar,
effective resolution-time membership, locked logical identities, shared layer-6
ability applicability and ordering, layer-7c composition, cleanup, capability
closure, privacy, rollback, and replay without runtime prose or card identity.
