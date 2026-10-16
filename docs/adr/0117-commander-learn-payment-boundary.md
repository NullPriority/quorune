---
title: "ADR 0117: Commander Learn payment boundary"
status: "ADR"
authoritative_source: "Commander Learn descriptor, capability shape, immutable rules-profile query and fixed payment owners"
verified: "2026-10-16"
audience: "rules, compiler, privacy and replay contributors"
maintenance: "hand-maintained"
adr_id: "0117"
decision_status: "accepted"
date: "2026-10-16"
---

# ADR 0117: Commander Learn payment boundary

## Context

CR 701.48a permits discarding one card to draw one card, or optionally obtaining
a Lesson from outside the game when no card was discarded. Commander rule
903.11 prevents the ordinary outside-the-game option. Treating Learn as an
unqualified rummage in every profile would erase a material rules distinction.

## Decision

The existing optional-payment operation gains a closed schema-5 action:
`action: learn`, `rules_scope: commander`, and the resolving controller. It
contains no caller-supplied cost, result, query or outside-game card reference.
This is a distinct action schema, rather than a spelling alias for ordinary
optional payments. Prior schema versions retain their payloads and behavior.

The compiler lowers only the complete Learn instruction, including its use in
otherwise represented spells, triggers and activations. Its capability is
available only to the Commander review and duel capability profiles. The
runtime additionally requires an immutable query fact from the authoritative
effective rules profile: `commander_duel` or `commander_multiplayer`. The
display-only format name and the capability review profile do not grant that
runtime authority. Missing, unknown or ordinary-format rules profiles fail
closed, as do changed action fields or a choice actor differing from the
locked resolving controller.

The Learn owner constructs one existing owned-hand discard payment and
delegates preparation, current-domain and incarnation checks, acceptance and
mutation intents to the fixed payment owner. Successful payment prepends one
existing private draw instruction. Declining, including with an empty hand,
produces neither a draw nor a Lesson. Canonical discard destination replacement
and draw replacement remain under their existing owners. The action does not
inspect a collection or search the card database at runtime.

This adds no universal operation, engine method, mutable-state cache, direct
GameState mutation, architecture exception or broadened trust rule. The exact
shape validator participates in both dependency closure and ordinary generated
program promotion; missing Learn, discard or draw ownership blocks admission.

## Alternatives

- An ordinary payment descriptor with no format boundary would incorrectly
  claim complete Learn support in traditional formats.
- A new universal Learn operation would duplicate an existing payment lifecycle
  for this bounded rules profile.
- Runtime Oracle parsing or a card-name special case would violate typed
  ownership and fail to compose across spells, triggers and activations.

## Consequences

Actual Igneous Inspiration damage precedes the actor-private Learn choice.
Pop Quiz draws its mandatory card first, and Professor of Symbology learns
through its normal entry trigger. Decline and empty-hand behavior, foreign-card
and wrong-principal rollback, pending save/load and pre-action replay protect
the complete action. Broader Learn subjects, unrepresented repetition,
outside-game retrieval and Learn replacement effects remain residual unless
their independent owners are represented.

## Removal condition

Replace this Commander-specific boundary only with a successor preserving
ordinary-format Lesson access, Commander restrictions, exact choice timing,
canonical discard/draw replacement, privacy and historical replay contracts.
