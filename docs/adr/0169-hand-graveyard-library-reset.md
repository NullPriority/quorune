---
title: "ADR 0169: Whole hand and graveyard library reset"
status: "ADR"
authoritative_source: "CR 121.2c, 400.7, 608.2c, 701.24 and 903.9b"
verified: "2026-11-15"
audience: "compiler, zone and replay contributors"
maintenance: "hand-maintained"
adr_id: "0169"
decision_status: "accepted"
date: "2026-11-15"
---

# ADR 0169: Whole hand and graveyard library reset

## Context

A complete hand and graveyard reset combines private and public origins. Every
selected library shuffles before a later all-player draw instruction begins.
The resolving spell remains on the stack. Commander destination replacement,
shuffle knowledge reset, draw restrictions and alternate cast prices already
have typed owners.

## Decision

Retain the existing recycling operation, intent and version-one handler. Extend
its inner specification with version two, requiring `include_hand: true` and a
fixed controller or all-player scope. Target-player new hand selection, subsets,
other origins and unknown fields fail closed. The version-one specification
retains its two-field encoding and the same stored dataclass fields; hand
membership is derived from the explicit version rather than stored separately.

Snapshot and validate every selected hand and graveyard before a single
canonical simultaneous move. Use existing replacement-aware zone movement, then
shuffle each designated library once even if no cards moved. Clients supply no
private object list. Shuffle and projection owners preserve hidden identities
and randomized order. A fine capability is inferred from the inner payload even
when coverage hints are absent.

Compose independently owned draw instructions after the complete reset. Draws
remain individual and follow active-player order. Terminal spell destinations,
Flashback, Assist and Entwine remain mandatory represented parts of the full
program. No source card identity selects generic reset behavior.

## Alternatives

- Moving each player and drawing immediately would let one player draw before
  another library is shuffled and would misorder replacement and trigger facts.
- Expanding the legacy payload implicitly would change historical graveyard-only
  records and permit hidden-zone access without an explicit producer.
- A second movement or shuffle implementation would duplicate replacement,
  knowledge, commander and replay ownership.

## Consequences

The compiler admits complete fixed hand-plus-graveyard instructions and their
already closed ordered compositions. Unknown siblings keep the whole original
unready. Historical graveyard-only payloads retain their previous meaning.
