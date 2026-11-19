---
title: "ADR 0176: Fixed draw and hand putback"
status: "ADR"
authoritative_source: "CR 121.2, 401.4, 608.2c, 609.3 and 701.24c; pinned Oracle/rulings"
verified: "2026-11-16"
audience: "compiler, drawing, private choice, zone and replay contributors"
maintenance: "hand-maintained"
adr_id: "0176"
decision_status: "accepted"
date: "2026-11-16"
---

# ADR 0176: Fixed draw and hand putback

## Context

A fixed draw followed by a hand putback uses the entire current hand, including
older cards. Blocked or partially completed draws do not remove the later
instruction. Failed-draw state actions wait until that instruction completes.
A mandatory effect returns as many cards as possible, unlike a payable cost.

## Decision

Compile the complete closed clause into ordinary drawing followed by one new
explicit version-one fixed putback instruction. Its private choice owns exact
selection, current incarnations and the owner's permutation. A placement choice
applies to the whole group and precedes the card-order choice without repeating
the draw or granting priority between choices.

Use existing simultaneous movement, owner-validated library ordering and queued
ordinary shuffle effects. The new fixed_hand_putback_v1 order tag and completed
choice value kind leave all prior DTO field sets and old tags unchanged. Existing
completed-choice replacement continuation retains unpaid movement and the
following shuffle. A selected card redirected elsewhere still causes its
requested shuffle; an impossible singular selection from an empty hand does not.

Require the fine capability from the raw instruction independently of coverage.
Delegate its validation through the existing library capability facade. Add no
new mutation owner, registry, runtime Oracle parsing or card-identity behavior.

## Alternatives

- Restricting selection to drawn cards would reject older legal hand cards.
- Optional selection would weaken a mandatory effect.
- One top and one bottom would violate a shared placement instruction.
- Reusing unmarked old top/redraw fields would alter historical meanings.
- Shuffling an empty impossible selection would contradict the pinned ruling.

## Consequences

Normal registration retains every material sibling. Private pending replay,
commander redirection, partial draws, deferred state actions, rollback and
library-edge order use the existing typed owners and focused behavioral proof.
