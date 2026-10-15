---
title: "ADR 0115: typed Manifest entry and turn-up"
status: "ADR"
authoritative_source: "Manifest instruction, typed zone transition and face-down action owners"
verified: "2026-10-14"
audience: "rules, compiler and replay contributors"
maintenance: "hand-maintained"
adr_id: "0115"
decision_status: "accepted"
date: "2026-10-14"
---

# ADR 0115: typed Manifest entry and turn-up

## Context

Manifest changes a physical library card's characteristics before entry. An
instant can enter as a face-down creature, while a creature's printed entry
abilities must remain inactive. Manifest dread additionally exposes a private
top-card choice and puts unmanifested looked-at cards into the graveyard after
the chosen entry. Turning a manifested creature face up uses its printed mana
cost as a priority-bound special action and retains represented Morph or
Disguise alternatives.

## Decision

The compiler lowers closed current-controller instructions to one typed
Manifest choice family. Its immutable query sees only the controller's library
snapshot. The existing zone-move intent gains an optional Manifest entry flag,
paired with the selected library incarnation and controller; absent flags keep
their historical identity serialization. The canonical zone-transition owner
prepares a face-down prospective characteristic view without changing the
authoritative origin object, then marks the committed battlefield object before
entry events. Replacement snapshots distinguish current-card face-down views
from newly allocated prospective tokens.

The existing face-down action owner shares payment, authentication, priority,
atomic turn-up, trigger production and replay. Revealing unsupported face-up
behavior requires current whole-card closure; printed text is never parsed by
the gameplay transaction. Complex mana, unsupported card forms and face-up
programs remain unavailable within this bounded action contract.

## Alternatives

Marking the authoritative library object face down before replacement would
change hidden state before the transaction was accepted. A separate movement
executor would duplicate entry, replacement, incarnation and journal behavior.
Token creation would lose the original physical card and its later turn-up
authority. The typed prospective view retains one canonical zone owner.

## Consequences

Face-down entry reuses zone replacement, visibility, incarnation and journal
ownership. Private inspection remains a semantic choice and cannot authorize a
foreign principal. Multiple Manifest instructions retain printed sequential
entry. Typed entry views are narrowly validated against the current library
object, and unknown variants fail closed. No token identity or card-name
dispatch selects gameplay behavior.

The change extends the existing zone-intent schema and face-down state model;
current-version action replay and the historical provenance boundary require
separate executable witnesses before trust promotion.
