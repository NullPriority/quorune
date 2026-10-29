---
title: "ADR 0132: untargeted land untap choices"
status: "ADR"
authoritative_source: "fixed land choice model and canonical tap-state intents"
verified: "2026-10-27"
audience: "compiler, choice, tap-state and replay contributors"
maintenance: "hand-maintained"
adr_id: "0132"
decision_status: "accepted"
date: "2026-10-27"
---

# ADR 0132: untargeted land untap choices

## Context

A fixed instruction to untap up to a number of lands chooses those lands while
resolving. It does not target them or implicitly restrict their controller.
Turning the instruction into a target schema changes timing, legal choices,
targeting restrictions and triggers. The existing tap-state batch already owns
simultaneous attempts, stun replacement, restrictions and occurrence discovery.

## Decision

Represent an exact `choose_untap_lands` instruction with a fixed maximum and
explicit any-controller or resolving-controller scope. A distinct fine-grained
choice capability depends on the existing untap-effect owner. The public choice
pins eligible physical and logical identities in canonical reference order,
accepts a distinct bounded set
including zero, and revalidates current land type, controller scope and phasing.
An empty eligible set completes without a choice.

Commit through `SetPermanentsTappedIntent`. The existing tap-state owner applies
every attempt before dispatching the resulting occurrences. The choice does
not create a new mutation owner, priority window or target binding. Normal
ordered composition retains earlier and later draw, discard, counter or bounce
instructions and the whole effect's real target legality.

Preserve the existing standalone controller-discard declaration when a
draw/discard pair participates in a larger program. A balanced top-level comma
partition may express an action list only when every complete clause has its
own typed proof. Existing conditional, linkage, target-count and bounded-program
guards remain authoritative.

## Alternatives

Using targets rejects legal untargetable lands and chooses too early. Reusing
the permissive historical public-object continuation does not pin current
incarnations or own untap semantics. A card-specific executor duplicates the
existing batch and ordered-continuation owners.

## Consequences

Source departure does not cancel a queued ability. Stun counters and current
untap restrictions retain ordinary ownership; step-only restrictions do not
become absolute prohibitions. New choices preserve principal authority, public
object projection, atomic rejection and pending replay. Variable quantities,
other permanent predicates and unrepresented siblings remain compiler
residuals. Historical operations and unsigned legacy draw/discard pair shapes
retain their existing contracts.
