---
title: "ADR 0156: versioned library-order knowledge"
status: "ADR"
authoritative_source: "Fixed library-selection intent and positional-knowledge owners"
verified: "2026-11-02"
audience: "rules, privacy, persistence and replay contributors"
maintenance: "hand-maintained"
adr_id: "0156"
decision_status: "accepted"
date: "2026-11-02"
---

# ADR 0156: versioned library-order knowledge

## Context

CR 401.4 makes simultaneous library placement ordering private to its owner.
CR 701.20a, b, d and e distinguish revealing or looking at identities from
continuing to reveal cards after reordering. Random ordering cannot disclose
the resulting permutation. The former fixed-selection intent retained owner
knowledge after random bottom ordering and retained public knowledge after a
privately chosen ordering of a revealed set. Principal projection then exposed
those cards at their actual new library positions.

Correcting the authoritative knowledge changes serialized state and command
hashes. Historical execution and strict declared runtime provenance remain
separate requirements.

## Decision

New games carry `library_selection_knowledge_version: 1`, mirrored in Game
Record format provenance. Absence selects the previous bookkeeping and remains
absent on serialization. The state codec accepts only absence or integer one.
Manifest provenance must agree with the initial checkpoint; unknown and boolean
values reject before replay.

The existing fixed-selection intent asks a pure policy owner for the remainder's
permitted positional viewers after canonical commit. In current games, a random
remainder containing multiple cards clears all positional viewers. An
owner-chosen multi-card bottom order retains only its owner's knowledge and
ceases public revelation. Zero or one remainder card creates no hidden
permutation and retains legitimate existing knowledge. Private chosen ordering
remains owner-known. Cards outside the remainder and public selected hand cards
retain their independent knowledge.

The existing choice, identity, partition, zone-transition, randomization and
projection owners remain unchanged. Bottom randomization does not become a
library shuffle. No new mutation owner, runtime Oracle parser, card identity
dispatch or capability registry is introduced. Declared runtime-trust identity
continues to reject mismatches; historical records are not rewritten to obtain
current execution authority.

## Alternatives

A projection-only filter leaves authoritative knowledge incorrect and cannot
distinguish legitimate singleton or unaffected-card knowledge. Clearing the
whole library destroys unrelated known positions. Reusing the shuffle owner
changes gameplay events and statistics. An unconditional correction changes
historical command hashes. Coupling this repair to another state version would
make its compatibility depend on unrelated behavior.

## Consequences

Normally admitted original spells retain their selection and movement behavior
while current packets stop disclosing hidden permutations. Pending load,
principal rejection, invalid-partition rollback and exact replay remain under
existing authority. This correctness repair adds no Oracle grammar or support
harvest and does not change the feature delivery floor.
