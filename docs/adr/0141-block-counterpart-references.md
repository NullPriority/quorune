---
title: "ADR 0141: block counterpart references"
status: "ADR"
authoritative_source: "immutable block transitions and semantic reference resolution"
verified: "2026-10-29"
audience: "rules, compiler and replay contributors"
maintenance: "hand-maintained"
adr_id: "0141"
decision_status: "accepted"
date: "2026-10-29"
---

# ADR 0141: block counterpart references

## Context

CR 509.3 distinguishes aggregate blocking events from one occurrence per
blocking relationship. A result referring to the other creature in a pair
cannot use the observing source or an aggregate list of blockers. The existing
block transition already retains every participant and assignment immutably.

## Decision

Compile the closed source-self paired head as subscriptions to `creature.blocks`
and `creature.becomes_blocked_by`. The blocking subscription explicitly depends
on its `blocked_attacker` fact, retaining the existing pair multiplicity owner.
The blocked subscription uses the existing per-blocker occurrence rather than
aggregate `creature.becomes_blocked`.

Introduce the explicit `$event.block_counterpart` reference. Stack creation
decodes the existing block transition, validates the selected actor/opposite
assignment, and seals the counterpart's public reference and logical identity.
Resolution returns that incarnation only while it remains on the battlefield
and available; a returned object is not a substitute. Existing event-object
tokens and historical frames retain their interpretation.

The first result shape grants one independently validated fixed keyword until
end of turn through the existing characteristic owner, without a target schema.
It requires a separate counterpart capability. The transition, trigger batch,
layer system and mutation owners remain canonical.

## Alternatives

- Composing aggregate block events loses per-creature multiplicity.
- Looking up a current combat relationship at resolution loses departed pairs
  and can select a different incarnation.
- Using the source reference changes which creature receives the result.

## Consequences

Blocking several attackers and being blocked by several creatures retain
separate relationships. Put-onto-battlefield-blocking behavior remains owned by
the existing event producer. Attached observers, characteristic-qualified
counterparts, delayed results and broader result sequences require independent
compiler and behavioral evidence.
