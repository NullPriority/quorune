---
title: "ADR 0090: typed public event-effect triggers"
status: "ADR"
authoritative_source: "normalized public occurrence producers and typed event-effect compiler"
verified: "2026-08-29"
audience: "rules, compiler, combat, activation, replay, and architecture maintainers"
maintenance: "hand-maintained"
adr_id: "0090"
decision_status: "accepted"
date: "2026-08-29"
---

# ADR 0090: typed public event-effect triggers

## Context

The generic typed event-effect body owner already resolved independently exact
effects, but many public trigger clauses stayed residual because their event
carriers were narrower than the authoritative occurrences the engine already
committed. Measuring the optional body alone overstated closure: a closed “you
may” effect does not make an unsupported attack, block, Cycling, or zone-event
binding executable.

## Decision

Compile a public trigger only when both its event binding and its typed body are
closed. Zone changes expose immutable previous or current controller, type,
subtype, color, token, and integer-power facts already sealed by the canonical
zone transaction. Damage and player-draw bindings consume their existing
committed result events. Spell-cast schema v4 adds the active phase to the same
immutable cast occurrence used by all other cast predicates.

Completed attack and block declarations, Cycling activations, and turn-face-up
special actions dispatch through one `trigger.event.normalized_public_action`
capability. Attack and block participants seal effective public keywords before
declaration completion. A creature emits one `creature.blocks` occurrence per
block assignment and one `creature.becomes_blocked` occurrence per declaration,
regardless of blocker count. Cycling captures only its publicly revealed hand
source snapshot before paying the discard cost, then places resulting triggers
above the activated ability. Face-up actions notify every current battlefield
semantic source after the object is turned face up.

All resulting programs enter the existing ordinary APNAP trigger batch and use
the existing optional-choice, target, and effect mutation owners. The compiler
probe asks this integrated owner for exact capability closure; body-only matches
do not count as harvest outcomes.

## Alternatives

- Add card-specific trigger handlers. Rejected because each listed occurrence
  is public, reusable grammar with an existing authoritative producer.
- Reparse Oracle text during combat or activation. Rejected because runtime
  authority belongs to typed programs and immutable event facts.
- Count every optional body as supported. Rejected because an effect body
  cannot certify its event carrier.
- Recompute characteristics while matching a zone event. Rejected because the
  canonical transaction already owns the cycle-safe effective snapshot.

## Consequences

Closed public zone, damage, draw, attack, block, Cycling, face-up, and fixed
cast-fact clauses can share one typed event-effect composition path. APNAP,
privacy, replay, and rollback remain owned by existing transactions. Historical
spell-cast schemas v1 through v3 and attack/block participant records without
keyword fields remain readable.

Attachment-relative, aggregate “one or more,” intervening-if, reflexive,
delayed, combined, chosen, history-relative, target-event, tapped-event,
counter-placement-event, secret, unsupported dynamic-comparison, and
declaration-replacement forms remain material residuals. Discard and sacrifice
bindings also remain residual until every authoritative producer supplies an
explicit typed cause; ordinary hand-to-graveyard or battlefield-to-graveyard
movement must never be used to infer either action. Dynamic power is trusted
only when the zone transaction supplies a sealed integer result; an unavailable
or noninteger value fails the comparison closed.

## Removal condition

Replace this design only if a successor preserves integrated carrier-and-body
closure, immutable public occurrence ownership, one becomes-blocked occurrence
per attacker, Cycling last-known hand identity, schema-compatible replay,
ordinary APNAP placement, privacy, and the same explicit exclusions without
runtime prose or card identity dispatch.
