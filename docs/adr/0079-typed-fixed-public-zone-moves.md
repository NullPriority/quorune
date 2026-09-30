---
title: "ADR 0079: typed fixed public-zone moves"
status: "ADR"
authoritative_source: "public-zone move descriptors, Commander zone rules, and capability registry"
verified: "2026-09-29"
audience: "rules, compiler, runtime, replay, and architecture contributors"
maintenance: "hand-maintained"
adr_id: "0079"
decision_status: "accepted"
date: "2026-08-21"
---

# ADR 0079: typed fixed public-zone moves

## Context

Quorune had distinct typed owners for direct battlefield exile, direct
battlefield return, and one-card own-graveyard return, but no shared boundary
for mandatory public graveyard exile or fixed public affected sets. Mass exile
and owner-hand return therefore remained residual even when their object query,
destination, and replacement behavior were already representable.

Commander movement exposed the correctness prerequisite. CR 903.9b replaces a
commander's move to hand or library before commit, while CR 903.9a offers one
state-based command-zone choice after each new graveyard or exile incarnation.
Excluding commanders from a generic mass effect would be wrong, and adding the
Oracle grammar without these owner-scoped choices would make Commander-profile
trust unsound.

## Decision

`PublicGraveyardCardTargetSpec` owns one canonical public-graveyard card target
with a closed card-type, card-type-union, or excluded-type predicate. It lowers
to the shared target system and the `exile_public_graveyard_card` operation.
Target advertisement and resolution use the same schema; execution delegates
the physical move, destination replacement, incarnation, event, and journal to
the existing single-object zone-transition owner.

`PublicZoneMoveSetSpec` owns fixed public battlefield or graveyard sets. Its
descriptor contains the exact origin, requested destination, immutable
`ObjectQuerySpec`, owner/controller relation, target seat when present, and
source exclusion. Schema version 2 additionally carries a closed prospective
battlefield controller policy and exact tapped result for graveyard-entry
sets. Selection freezes one APNAP-ordered identity set, revalidates every
member, and delegates the complete batch to
`ZoneTransitionOwner.move_cards_simultaneously`. Empty sets are ordinary
no-ops. No object moves before every represented replacement choice is ready.

The same typed object boundary owns two adjacent cases rather than adding
special zone branches. `PutTypedCardFromHandHandler` projects one private,
owner-pinned hand choice to the actor, orders eligible refs canonically,
revalidates the current object, and submits the normal single-object move.
`SetPublicTapStateSetHandler` locks a current public permanent set and submits
the fixed result through the existing tap/untap owner, including stun-counter
replacement. Neither handler interprets Oracle text at runtime.

`commander_zones.py` owns CR 903.9 identity and choice models. A designated
physical commander's hand/library event receives an owner-optional command-zone
replacement. Each new graveyard/exile logical incarnation receives one public,
owner-scoped state choice; declining marks only that incarnation. The generic
public choice owner handles projection, stale-response rejection, rollback,
journaling, and replay.

The compiler accepts only mandatory closed wording across spell, triggered,
activated, modal-body, and sequence contexts. Capability closure requires the
typed target/set operation, target revalidation where applicable, canonical
destination replacement, and Commander zone-return boundary. The runtime never
parses Oracle prose or dispatches on card identity.

## Explicit exclusions

- optional or variable quantities, chosen subsets, random selection, and
  multiple destinations;
- dynamic-count, chosen-quality, numeric-characteristic, attachment-expanded,
  and open exception predicates;
- graveyard entry that may contain Auras, needs attachment choices, adds
  counters, or otherwise crosses an unrepresented entry interaction;
- hidden origins other than the closed actor-private hand choice, linked
  results, delayed return, and temporary exile;
- private choices for Auras, arbitrary permanents, nonpermanents, multiple
  cards, attacking entry, or later linked use of the selected identity;
- dynamic or chosen tap predicates, continuous untap prohibitions, later-step
  schedules, and optional tap-or-untap results;
- merged and melded Commander cases governed by CR 903.9c;
- a Commander state choice combined with an independently unrepresented
  zone-moving state-based action.

These remain material residuals or explicit trust exclusions. Dynamic
characteristic counts do not enter this decision's affected-set or target
boundary.

## Alternatives

- Add separate runtime branches for graveyard exile, mass exile, mass return,
  and Commander movement. Rejected because they share one zone-transition and
  replacement substrate and would duplicate identity, APNAP, and replay logic.
- Exclude commanders from fixed public sets. Rejected because the Oracle
  instruction affects them; CR 903.9 changes their destination through owner
  choice rather than removing them from the affected set.
- Recover target or affected-set grammar from Oracle text at resolution.
  Rejected because authoritative behavior must consume typed compiled data.
- Admit dynamic, linked, delayed, hidden-origin, or reanimation forms through
  the same operation. Rejected because each crosses a distinct choice,
  characteristic, continuation, or visibility boundary.

## Consequences

One shared boundary now covers direct typed public-graveyard exile, fixed
graveyard sweeps, fixed battlefield exile and owner-hand return, closed public
graveyard entry, one private controller-hand entry, and fixed public tap-state
sets without creating another zone, choice, or tap engine. Four-player tests
cover typed advertisement, APNAP replacement choice, Commander choices,
privacy, stale identity rollback, stun replacement, prospective controller
facts, and exact replay. Capability and reusable-piece evidence are bound to
those behavioral tests.

The exact full-corpus card, ability, and residual deltas remain owned by the
generated compiler/frontier reports. Broader hidden-zone, linked-result,
attachment-dependent entry, and dynamic tap-state families require their own
typed prerequisites before admission.
