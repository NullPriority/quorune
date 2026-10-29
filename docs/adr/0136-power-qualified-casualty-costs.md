---
title: "ADR 0136: Power-qualified Casualty costs"
status: "ADR"
authoritative_source: "typed zone-cost model, Casualty component and existing cast-copy owners"
verified: "2026-10-28"
audience: "rules, compiler and persistence contributors"
maintenance: "hand-maintained"
adr_id: "0136"
decision_status: "accepted"
date: "2026-10-28"
---

# ADR 0136: Power-qualified Casualty costs

## Context

Casualty combines an optional sacrifice price and a cast trigger. Creature
power can change when the casting source leaves its origin, so an offer based
on the earlier hand size can disagree with payment. The existing casting
sequence prepares the spell on the stack before paying costs. Sacrifice
replacement choices may suspend that sequence, and a pending copy trigger
must survive the original spell leaving the stack.

## Decision

Zone-change additional-cost schema four uses the existing fixed-cost field set
with a closed predicate: sacrifice one controlled creature whose effective
power meets a fixed minimum. Earlier schemas retain their codecs. The new
structural capability belongs to this predicate and delegates selection,
payment, replacement and commit to the existing zone-cost owners.

A registered `FixedCasualtySpec` identifies one complete printed declaration.
It creates an ordinary optional cost option. In new games, numeric price
queries use the existing prospective source-placement characteristic reader;
the existing historical placement mode remains authoritative for older games.
Payment still revalidates the actual current creature.

The selected creature's logical incarnation is sealed before payment. Spell
context preparation carries the fact without claiming the cost has committed.
Cast-trigger construction compares it with the canonical committed cost
objects and stores one ordinary spell-copy template. The existing copy trigger
router handles targets, X, private body choices and replay. Copies are not cast
and do not create further Casualty triggers.

## Alternatives

A resolution-time keyword flag cannot represent an additional cost or its
independent cast trigger. Sacrificing during offer generation would mutate a
read-only query and violate casting order. Validating committed payment during
stack preparation would reject legal casts before the cost owner can run.
Retaining only a physical reference would lose the chosen incarnation. A
separate copy implementation would duplicate target, continuation and replay
authority. The chosen extension keeps these responsibilities with their
existing owners.

## Consequences

The declaration, power-qualified price, ordinary spell body and copy engine
remain independently capability-bound. Tapped and summoning-sick creatures
can pay sacrifice; a redirected destination does not require a death.
Prospective source placement and actual payment read the same numeric state.
No new game-state mode, generic callback registry or instruction interpreter
is introduced. Whole-card support requires executable evidence for both the
ordinary body and the paid-copy behavior.

Multiple or granted instances, variable permanent-copy exceptions and unknown
companion bodies remain explicit unfinished support under the broader goal.
