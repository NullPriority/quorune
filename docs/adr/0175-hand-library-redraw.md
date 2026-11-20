---
title: "ADR 0175: Hand library returns and redraw"
status: "ADR"
authoritative_source: "CR 121.2c, 401.4, 614.6, 701.24 and 903.9b; pinned Oracle and rulings"
verified: "2026-11-16"
audience: "compiler, private choice, zone, drawing and replay contributors"
maintenance: "hand-maintained"
adr_id: "0175"
decision_status: "accepted"
date: "2026-11-16"
---

# ADR 0175: Hand library returns and redraw

## Context

A hand can return to its library by shuffling or by private bottom placement.
The collection can be the whole hand or a chosen subset. Drawing that many
depends on committed arrivals after replacement; a commander redirected to the
command zone did not enter the library. An empty shuffle remains a shuffle.

## Decision

Use a new explicit semantic-choice instruction with a closed version-one
placement, selection, recipient and zero-or-one offset descriptor. Seal current
hand incarnations before choices and movement. Keep selection and permutation
private to the owning player. A scheduled event player can choose their hand
while the stack controller remains the original source controller.

Delegate simultaneous movement to the existing zone owner. Privately ordered
bottom returns use an explicitly tagged hand-origin subtype of the existing
owner-validated library-arrival order. Count only selected original identities
actually present in their owner library after the committed batch. Complete all
affected shuffles before appending ordinary APNAP draw instructions.

Reuse typed result-bound preparation and replacement continuations. The new
draw-result adapter is a subtype of the existing adapter interface; old discard
result types, intent fields, library-order payloads and replay meanings remain
unchanged. Payload ownership requires the fine capability without coverage
annotations, with scheduled-player authority for that explicitly bound scope.

Library capability projection delegates the existing library/order/search
validators through a focused facade. Its immutable coverage projection replaces
the corresponding entries in the old central map; it adds no second registry.

## Alternatives

- Counting the initial hand would overdraw when a library move is replaced.
- Moving and drawing for one player before another shuffle would change ordering.
- Reusing an unmarked discard or graveyard instruction would alter old records.
- Revealing choices or saved permutations would expose private hand information.

## Consequences

Natural draw precedes a draw-step trigger entering the stack. Repeated triggers
take separate current hand snapshots. Source activation prices, material card
siblings, draw replacements, player departure, rollback and private pending
replay retain their existing owners. Extra riders and unrepresented predicates
remain material residuals.
