---
title: "ADR 0102: typed fixed combat-entry lifecycles"
status: "ADR"
authoritative_source: "typed casting, activation, attack, token, and zone-transition owners"
verified: "2026-09-22"
audience: "rules, compiler, combat, activation, replay, and architecture maintainers"
maintenance: "hand-maintained"
adr_id: "0102"
decision_status: "accepted"
date: "2026-09-22"
---

# ADR 0102: typed fixed combat-entry lifecycles

## Context

Ninjutsu, Commander ninjutsu, Encore, Blitz, Sneak, Web-slinging, and
Myriad share public combat-entry facts, but they do not share one Oracle-text
execution rule. Treating them as unrelated card features would duplicate cast,
activation, return-cost, token, trigger, and delayed-cleanup authority.

## Decision

Compile only isolated fixed ordinary-mana declarations (and bare Myriad) into
source-spanned typed descriptors. Blitz, Sneak, and Web-slinging extend the
existing cast-lifecycle cost and resolution owner. Ninjutsu and Encore extend
the compiler-pinned activation catalog. Myriad remains an ordinary normalized
self-attack trigger and uses the existing semantic-choice and token owners.

The selected unblocked attacker is queried and paid through the canonical
object-cost path. Its public attack recipient is frozen before the cost changes
zones, so Ninjutsu and Sneak can enter tapped and attacking that same recipient
without emitting a new attack declaration. Ninjutsu reveal visibility is tied
to the source incarnation and stack object, including counter cleanup.

Myriad freezes copyable characteristics when its attack trigger is created.
Token creation consumes that public last-known snapshot, resolves replacement
effects through the ordinary token transaction, and schedules end-of-combat
exile for each resulting incarnation. Encore copies its public exiled cost
object per current opponent, grants Haste, records an opponent-specific
attack-if-able designation, and schedules identity-pinned next-end-step
sacrifice. Blitz records a noncopiable designation only on the resolved
permanent; its dies trigger uses the permanent's last-known controller.

No runtime path parses Oracle text, dispatches by card identity, or creates a
second casting, activation, trigger, token, combat, or zone-transition engine.

## Alternatives

- One handler per printed card was rejected because the mechanics share typed
  public foundations and broad reusable grammar.
- A single runtime keyword switch was rejected because the mechanics have
  distinct costs, timing, trigger, and cleanup semantics.
- Copying Myriad's current object at resolution was rejected because the
  source may have left the battlefield; its trigger needs last-known copyable
  characteristics.

## Consequences

Supported cards receive exact offer/commit parity, atomic costs, APNAP trigger
placement, replacement-aware tokens, logical-object-pinned cleanup, public
projection, checkpointing, and replay through existing owners. The token-copy
intent gains an additive optional copy snapshot; historical identities without
that field remain readable.

Variable, hybrid, Phyrexian, snow, nonmana, modified, conditional, repeated,
granted, copied, text-changed, team-specific, or otherwise noncanonical forms
remain residual. Copy modifications outside the canonical copyable snapshot
and interactions outside existing typed replacement or permission owners also
remain excluded.

## Removal condition

Replace this design only if a successor preserves source-spanned compilation,
one current-ability boundary, canonical atomic costs, last-known copy state,
same-recipient combat entry, replacement-aware tokens, APNAP placement,
incarnation-pinned cleanup, privacy, and exact replay without runtime prose or
card identity dispatch.
