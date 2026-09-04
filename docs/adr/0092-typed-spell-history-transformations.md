---
title: "ADR 0092: typed spell-history transformations"
status: "ADR"
authoritative_source: "paired Daybound/Nightbound compiler nodes, previous-turn history, and permanent transform owner"
verified: "2026-09-04"
audience: "rules, compiler, turn, object-identity, replay, and architecture maintainers"
maintenance: "hand-maintained"
adr_id: "0092"
decision_status: "accepted"
date: "2026-09-04"
---

# ADR 0092: typed spell-history transformations

## Context

Daybound and Nightbound couple a public game designation, the previous active
player's spell count, nonmodal double-faced entry, immediate face
synchronization, and the no-priority untap interval. Earlier Innistrad
Werewolves use a separate pair of intervening-if upkeep triggers: no spells by
any player, or two or more spells by one player, during the previous turn.

Treating either family as ordinary Oracle-trigger prose would duplicate turn
history and face mutation, miss CR 701.27f's stale-instruction rule, and make
save/load or APNAP behavior depend on runtime text. Treating a face change as a
zone move would incorrectly create a new object and discard counters, damage,
attachments, controller, and timestamp.

## Decision

Add one closed universal `transform` semantic operation. The selected compiler
grammar may emit it only for a source-self legacy upkeep trigger together with
the source logical-object placeholder and a captured nonnegative transform
count. The generic semantic handler remains read-only and delegates the actual
face mutation to `permanent_transform.py`. That owner accepts only a phased-in,
face-up, nonmodal double-faced card permanent with a permanent opposite face,
preserves object-local state, increments the transform count, and dispatches a
normalized public transform event after every member of a simultaneous batch
has changed face.

`turn_history.py` retains a bounded per-player spell-count summary for exactly
the immediately previous turn. The day/night untap owner reads only the
previous active player's count; the legacy trigger predicate independently
reads the all-player total or per-player maximum. The latter is checked both at
trigger time and resolution. An old trigger cannot transform a source that
left and re-entered or one that transformed after the trigger was put on the
stack.

Paired Daybound/Nightbound nodes use the shared current-ability fragment
applicability query. Entry at night selects the trusted Nightbound face before
entry characteristics are finalized. Initial Daybound establishes day;
initial Nightbound establishes night only in the absence of an applicable
Daybound permanent. Designation transitions and bound transformations occur
before ordinary untap selection, and all resulting triggers wait for the
existing upkeep APNAP batch.

The compiler-corpus owner also declares `semantic_imports`. Package initializer
files remain identity inputs but their re-export-only runtime imports are leaf
boundaries, matching the compiler-version sentinel. Direct compiler imports
remain content-bound. This removes the demonstrated false invalidation where
a runtime-only characteristic-host correction discarded an immutable corpus
result.

## Alternatives

- Represent transformation as a battlefield-to-battlefield zone move.
  Rejected because CR 712.18 preserves the same object and all attached state.
- Store only an aggregate previous-turn spell count. Rejected because
  Daybound/Nightbound and the legacy two-spell condition ask different
  per-player questions in multiplayer.
- Dispatch card-name-specific Werewolf behavior. Rejected because names are
  compiler evidence, not runtime authority.
- Make Daybound/Nightbound a family-specific layer-6 check. Rejected because
  removal and restoration must use the same static-component applicability
  query as every other represented ability.
- Follow every package export in the compiler corpus cache identity. Rejected
  because it repeats an expensive census for runtime-only edits that the
  compiler identity sentinel already proves nonsemantic.

## Consequences

The bounded family covers paired Daybound/Nightbound cards and the two exact
legacy each-upkeep wordings while preserving entry timing, multiplayer spell
counts, current ability presence, same-object face changes, attachments,
APNAP placement, public projection, rollback, save/load, and exact replay.
`CommanderEngine`, direct and unowned GameState writes, runtime Oracle access,
card-identity dispatch, and oversized symbols do not grow.

Explicit make-day/make-night effects, shared-team turns, arbitrary, targeted,
mass, optional, delayed, compound, transform-into, and Convert grammar,
double-faced tokens, copied or granted characteristics, face-down and phased
objects, modal, melded, or merged forms, and instant or sorcery destination
faces remain outside this trust boundary. Independently unsupported sibling
abilities continue to keep their complete CardPrograms residual.

## Removal condition

Retire the `transform` operation only if a successor preserves the same closed
source grammar, source incarnation and transform-count checks, current ability
applicability, bounded previous-turn facts, non-zone-change object identity,
simultaneous event timing, APNAP, privacy, rollback, and replay without runtime
Oracle interpretation or card-specific dispatch.
