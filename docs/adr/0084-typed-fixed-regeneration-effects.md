---
title: "ADR 0084: typed fixed regeneration effects"
status: "ADR"
authoritative_source: "typed regeneration compiler, semantic handler, and destruction transaction"
verified: "2026-08-27"
audience: "rules, compiler, replay, and architecture contributors"
maintenance: "hand-maintained"
adr_id: "0084"
decision_status: "accepted"
date: "2026-08-27"
---

# ADR 0084: typed fixed regeneration effects

## Context

ADR 0065 established identity-pinned regeneration shields and the canonical
destruction disposition for the exact self-creature activation. The current
frontier also contains a coherent fixed family: direct artifact, creature, or
permanent targets; the creature attached to an Aura or Equipment; the named
source permanent; and exact direct-target or fixed-set destruction followed by
a cannot-be-regenerated rider. These forms reuse the existing shield and
destruction owners but require different compiled object references and an
explicit prohibition carried by the destruction transaction.

## Decision

The compiler lowers exact fixed regeneration instructions to the universal
`regenerate` operation with one of three closed references: a revalidated
`$target.0`, a typed current-or-last-known source-attachment descriptor, or
`$source.zone_object`. The version-2 regeneration handler pins source identity
when the resolved object is the source and otherwise delegates resolution of
the already-validated public reference to the regeneration owner. It never
interprets Oracle text or infers an attachment or target.

An exact immediately adjacent cannot-be-regenerated rider is represented by
`regeneration_prohibited: true` on the same direct-target or fixed-set destroy
effect and immutable destruction plan. That flag suppresses only the
regeneration disposition. Indestructible remains a destruction prohibition,
and an effect-destruction shield counter remains an applicable replacement.
A regeneration shield is not consumed when either of those outcomes preserves
the permanent. State-based destruction cannot carry the flag.

Ordinary destruction with both regeneration and a shield-counter replacement
still fails before mutation because the affected player must order applicable
replacements. The explicit prohibition removes regeneration from that choice;
it does not establish a general replacement-ordering policy.

## Alternatives

- Create separate targeted and mass regeneration engines. Rejected because
  reference resolution differs, but shield lifetime and mutation ownership do
  not.
- Consume a regeneration shield when regeneration is prohibited. Rejected
  because the replacement is inapplicable rather than applied unsuccessfully.
- Encode cannot-be-regenerated as a second sequential effect. Rejected because
  it modifies the same destruction event and must be present during preflight.
- Treat the prohibition as overriding Indestructible or shield counters.
  Rejected because it changes only whether regeneration can replace the event.

## Consequences

Fixed regeneration composes across spell, triggered, activated, modal,
attached-object, and named-source contexts through existing reference and
semantic owners. Direct and fixed-set destruction share one additional
capability and retain atomic preflight, simultaneous movement, projection, and
replay. ADR 0065 remains the historical decision that introduced the shield
model; this record supersedes only its compiler and prohibition exclusions.

Variable, repeated, optional, conditional, qualified, controller-relative,
and multiple-target regeneration remains residual. Static regeneration,
damage-linked or delayed prohibition, linked results, unsupported destruction
grammar, unsupported costs or target predicates, and ordinary competing
replacement ordering remain fail closed.

## Removal condition

Retire this boundary only if a successor preserves closed source, target, and
attachment references; identity and stale-resolution checks; exact
prohibition scope; Indestructible and shield-counter interactions; atomic
fixed-set destruction; cleanup, projection, rollback, capability closure, and
exact replay without runtime Oracle interpretation.
