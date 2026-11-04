---
title: "ADR 0146: intrinsic draw and cast lifecycle"
status: "ADR"
authoritative_source: "typed drawing, trigger binding, visibility projection and canonical casting owners"
verified: "2026-10-30"
audience: "drawing, casting, visibility and replay contributors"
maintenance: "hand-maintained"
adr_id: "0146"
decision_status: "accepted"
date: "2026-10-30"
---

# ADR 0146: intrinsic draw and cast lifecycle

## Context

Miracle links an optional first-draw reveal to a later casting choice. The
library object, resulting hand object, original reveal trigger and current
controller of a copied trigger have distinct roles. Enclosing draw instructions
must finish before casting, and a later hand incarnation cannot reuse the old
permission. Other reveal sources and remembered knowledge have independent
lifetimes.

## Decision

The existing draw-reveal registry owns the intrinsic descriptor. Both compiler
nodes require complete-card admission. A separate immutable continuation keeps
the prepared draw journal, exact turn and card facts, previous battlefield
reveal choices and instruction-tail actions. It resumes the single draw
trampoline established by [ADR 0145](0145-draw-reveal-choice-coordination.md).

The canonical draw transaction reveals before movement and emits the linked
event with the resulting hand identity. Trigger construction seals that subject
and the original trigger reference. Ongoing disclosure derives from the live
or pending original trigger and exact hand incarnation. Trigger copies retain
the cast subject while the original trigger controls reveal duration. This pure
query preserves other reveal sources and historical knowledge without restoring
an old audience over newer state.

The resolving trigger's current controller receives a typed cast-or-decline
choice. Its cost and target variants use the shared fixed resolving-cast query,
canonical proposal and ordinary cast commit. The authoritative alternative
replaces the mana base while additional costs remain applicable. Face-down
casting retains its separate source-ability suppression. The internal hand
authority exists only inside the verified resolving window and can therefore
handle a copy controlled by another player without creating an ordinary cast
method.

## Alternatives

Reusing battlefield-source continuations would conflate the source and drawn
card roles. Adding Miracle to the ordinary fixed hand-cast catalog would expose
its price outside the resolving trigger. Restoring a saved reveal audience on
trigger departure would overwrite independent later disclosures. Separate draw
or cast executors would duplicate existing authority and replay ownership.

## Consequences

Fixed ordinary-mana declarations share established replacement, APNAP, casting,
target, payment and spell-priority owners. Complex costs, granted or duplicated
declarations and unrepresented faces retain material residuals. Native evidence
uses original records and normal complete-program registration. Copied-trigger
fixtures identify the canonical copy-owner precondition separately from named
copy-card compiler support. All resulting choices and identities remain ordinary
Game Record v3 state.
