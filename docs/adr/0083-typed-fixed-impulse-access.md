---
title: "ADR 0083: typed fixed impulse access"
status: "ADR"
authoritative_source: "this decision record"
verified: "2026-08-25"
audience: "rules, compiler, zones, replay, and architecture maintainers"
maintenance: "hand-maintained"
adr_id: "0083"
decision_status: "accepted"
date: "2026-08-25"
---

# ADR 0083: typed fixed impulse access

## Context

Some resolving instructions exile a fixed number of cards from the top of the
resolving controller's library and allow that player to play the cards until
the end of the current turn or their next turn. Correct execution crosses a
private ordered zone, replacement-aware simultaneous public-zone movement,
ordinary land-play and spell-cast legality, object incarnation, duration
expiry, privacy projection, and replay. Treating the text as a one-shot free
cast, retaining references to cards that did not reach exile, or inferring the
permission from Oracle prose at runtime would give the wrong authority or
lifetime.

## Decision

Compile only exact mandatory fixed-count own-library instructions with
unrestricted play permission into a source-spanned CardProgram node carrying
the `fixed_impulse_access` semantic operation. The runtime handler validates
the closed descriptor and emits `ImpulseAccessIntent`; it does not receive
mutable GameState or interpret Oracle text.

The typed impulse-access owner snapshots the current top cards, delegates one
replacement-aware simultaneous move to `ZoneTransitionOwner`, and grants
permission only to actual exile results using physical and logical object
identity. CommanderEngine delegates ordinary land-play and spell-cast
permission queries to the owner, and cleanup delegates current-turn and
next-turn expiry. Existing targeting owns resolution revalidation, so an
instruction whose targets are all illegal does not execute the impulse effect.
The operation is registered in the universal semantic inventory and reviewed
in the exact architecture-guard baseline.

## Alternatives

- Reuse free-cast or one-shot exile effects. Rejected because impulse access
  uses ordinary play costs and timing and can authorize a land play.
- Store only printed card or Oracle identity. Rejected because permissions must
  follow the exact exiled incarnation and must not survive an intervening zone
  change.
- Check temporary annotations directly throughout CommanderEngine. Rejected
  because permission interpretation and duration belong to one typed owner.
- Parse the originating Oracle text when a card is played. Rejected because
  authoritative behavior must be compiler-backed and replayable without prose.

## Consequences

Fixed current-turn and next-turn impulse access composes across spell,
triggered, activated, and sequence contexts while retaining canonical zone,
cast, land-play, target, cleanup, privacy, rollback, and replay owners. The
operation adds no card-name, set, collector-number, or Oracle-ID dispatch; no
new unowned GameState write; and no runtime Oracle-text access. Permission
responsibility is delegated out of CommanderEngine, but responsibility and
line-count movement are separate facts. The #316 source transition changed
CommanderEngine from 7,064 to 7,065 logical lines (`+1`). The contemporaneous
architecture guard changed from an older reviewed 7,096-line allowance to the
7,065-line head (`-31`); that baseline rebind is not the pull request's source
delta.

Dynamic counts, another player's library, cast-only or free-cast permission,
type/color/mana-value/timing restrictions, one-of-many choices,
as-long-as-exiled durations, linked results, conditional or modal tails, and
non-source-spanned programs remain fail-closed. This decision does not claim
general exile permissions, library access, alternate costs, timing overrides,
or complete CR 406 support.

## Removal condition

Retire `fixed_impulse_access` only if a successor preserves exact source-spanned
grammar, immutable library-top planning, replacement-aware simultaneous
movement, actual-result incarnation binding, ordinary cast and land-play
ownership, current/next-turn cleanup, target revalidation, rollback, privacy,
capability closure, and exact replay without runtime Oracle interpretation.
