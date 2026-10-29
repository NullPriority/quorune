---
title: "ADR 0127: prospective public self-cost conditions"
status: "ADR"
authoritative_source: "self cost reduction model, prospective casting condition facts and shared public condition evaluator"
verified: "2026-10-27"
audience: "rules, compiler, casting and replay contributors"
maintenance: "hand-maintained"
adr_id: "0127"
decision_status: "accepted"
date: "2026-10-27"
---

# ADR 0127: prospective public self-cost conditions

## Context

Intrinsic spell discounts and static public conditions share public facts, but
their timing and source roles differ. CR 601.2a places the spell on the stack
before its total cost is determined. A graveyard or hand condition cannot count
the casting card in its old zone. The spell's casting controller also need not
be its owner.

## Decision

Self-cost metric schema five stores one existing immutable public-state
condition and returns its Boolean multiplier. Legacy metric precedence and
payloads remain unchanged. The compiler uses this form only after the existing
public and turn-fact metrics decline the phrase.

A read-only prospective view gives the source its stack zone and casting
controller, preserves its owner, and removes its identity from the prior zone.
The shared condition owner evaluates those facts before payment. Offers and
accepted proposals use the same cost path, and unavailable facts reject rather
than supplying a false or zero discount. Descriptor-owned minima independently
require `casting.cost.self_public_state_condition` and nested query authority.

Independent paired reduction sentences use the existing additive term list.
Both clauses must compile completely; they do not become a conjunction that
requires both predicates before either reduction applies.

## Alternatives

Extending a separate threshold grammar for every public condition would
duplicate existing rule authority. Reading the unchanged current zones would
count a casting card in its former graveyard. Mutating the actual game during
offer construction would violate query purity and rollback boundaries. The
prospective view supplies the needed facts without a second evaluator or payer.

## Consequences

The existing cast transaction still fixes total cost before paying it and owns
every mutation. The new condition and source view add no GameState field. Saved
programs retain their explicit metric version, and earlier metrics keep their
codec. Native evidence covers additive discounts, current offer revalidation,
opponent thresholds and a creature cast from its graveyard under a permission.
