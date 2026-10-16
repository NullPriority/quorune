---
title: "ADR 0118: paid entry-copy casting facts"
status: "ADR"
authoritative_source: "paid-entry cost component, cast option expansion, zone occurrence and canonical token-copy owners"
verified: "2026-10-16"
audience: "rules, compiler, copying, privacy and replay contributors"
maintenance: "hand-maintained"
adr_id: "0118"
decision_status: "accepted"
date: "2026-10-16"
---

# ADR 0118: paid entry-copy casting facts

## Context

Offspring has an optional additional cost and a linked entry trigger. The cost
is paid while casting; its trigger creates a token copy with base power and
toughness 1/1. A newly created permanent copy has the keyword but no paid-cast
fact. A copied spell instead inherits the original casting decisions under
CR 707.10. Confusing these paths can omit legal copies or create recursion.

## Decision

Compile a printed positive fixed ordinary-mana Offspring instance into two
source-spanned nodes: a complete-card-admitted cost component and a source
entry trigger. The immutable component names its compiler instance and fixed
mana vector. A paid option retains its base option identity and casting kind,
adds the fee before total-cost modifiers, and carries a closed payment fact.
Ordinary and unpaid options carry no new fact.

The accepted option owns the stack fact. Clients cannot supply an instance,
count, copied characteristic snapshot or payment marker. The existing spell
copy context preserves the fact with the other copied casting decisions.
At resolution the canonical object-state owner validates and marks this exact
stack incarnation. The zone owner seals that fact only for stack-to-battlefield
entry and publishes the instance to normalized source entry discovery. Zone
reset removes the object-local marker, so a later blink is an unpaid entry.
The optional occurrence field is omitted when absent, preserving older payloads.

The entry trigger uses the existing strict token-copy recipe with a source
reference and a 1/1 copiable-value exception. Current values, same-incarnation
last-known values on departure, replacement ordering, token creation, copied
ability behavior, state-based actions and private projection remain under
their existing owners. The paid trigger controller determines who creates the
copy even when the original permanent changes controller before resolution.

The initial production rejects variable, hybrid, nonmana, repeated, multiple
printed and unsupported face instances. Squad's repeated fees and simultaneous
copy count require a separately reviewed extension. These residuals remain
unfinished support. No universal operation, runtime Oracle parsing, card-name
dispatch, engine method, architecture exception or mutable-state cache is added.

## Alternatives

- Reuse a Kicker Boolean: rejected because the two keyword instances have
  distinct cost and trigger identities.
- Copy a paid marker with a permanent's copiable characteristics: rejected
  because an ordinary token copy was not cast with that additional cost.
- Read payment from the current permanent during trigger resolution: rejected
  because departure and re-entry cannot replace the original incarnation.
- Pay the fee after reductions: rejected because CR 601.2f determines the
  complete total before reducing and locking it.

## Consequences

Actual offered and accepted casts cover paid/unpaid prices, affordability and
cost reductions, source departure, blink and changed control while the entry
trigger is pending, inherited token abilities, four-player privacy and exact
pre-action replay. The immutable casting-copy seam separately checks inherited
paid decisions. Broader copied-spell gameplay requires its own admitted copy
producer; a direct owner seam is not a claim that such a card is implemented.

## Removal condition

Retire this fact only when a successor preserves independent paid instances,
copied casting decisions, fresh unpaid entries, source current/LKI semantics,
canonical token batches, rollback and historical replay behavior.
