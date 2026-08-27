---
title: "ADR 0085: typed fixed homogeneous target sets"
status: "ADR"
authoritative_source: "typed target-set compiler, selection owner, and semantic handlers"
verified: "2026-08-27"
audience: "rules, compiler, targeting, replay, and architecture maintainers"
maintenance: "hand-maintained"
adr_id: "0085"
decision_status: "accepted"
date: "2026-08-27"
---

# ADR 0085: typed fixed homogeneous target sets

## Context

Oracle instructions commonly apply one destroy, exile, return, tap, or untap
verb to a bounded set of interchangeable public targets. The scalar operations
already have typed legality and mutation owners, but compiling each plural
wording independently would duplicate cardinality, resolution revalidation,
replacement, replay, and privacy behavior. Public cards selected "from a
single graveyard" also need one relation across the whole target group rather
than a family-specific offer filter.

## Decision

Compile only exact, one-or-two, or up-to-one-through-six homogeneous target
sets. The compiler first asks the existing scalar leaf for its closed target
predicate and effect, then lifts that result to one `$targets` group. The
shared target-selection owner enforces distinct objects and, only when printed
grammar requires it, one public owner across the group at both offer
feasibility and submission validation.

Register separate strict universal operations for the seven represented typed
effects: `destroy_targets`, `exile_permanent_targets`,
`exile_public_graveyard_targets`,
`return_graveyard_targets_to_owner_hand`,
`return_permanent_targets_to_owner_hand`, `tap_targets`, and `untap_targets`.
Each handler validates the closed set descriptor and emits immutable intents;
canonical destruction, simultaneous zone transition, and scalar tap-state
owners retain replacement ordering and mutation authority. Runtime code does
not inspect Oracle prose or card identity.

The generated work-selection measurement derives the current cohort from the
frontier and records a content-fingerprinted source-transition receipt. Static
policy retains only grammar, owner, capability, and exclusion identities.

## Alternatives

- Add plural variants to every scalar parser and runtime handler. Rejected
  because cardinality and relational legality would diverge across effects.
- Model the set as a sequence of single-target effects. Rejected because
  destruction and zone movement must retain simultaneous planning and commit
  semantics, and one illegal target must not suppress legal survivors.
- Filter legal references to one owner during projection. Rejected because the
  player may choose any owner having a feasible group; legality belongs to the
  submitted set, not an arbitrary first projected owner.
- Parse plural Oracle text at resolution. Rejected because authoritative
  behavior must remain compiler-backed and replayable without prose.

## Consequences

The family composes across spell, triggered, activated, and modal carriers
while preserving scalar predicates, resolution revalidation, destination and
destruction replacements, rollback, privacy, and exact replay. The reviewed
operations add no `CommanderEngine` growth, direct or unowned GameState write,
runtime Oracle-text access, fixed card-identity dispatch, or oversized-symbol
growth.

Heterogeneous target roles, divided or dynamic quantities, repeated or random
selection, linked results, compound conditions, hidden zones, battlefield
returns, unsupported characteristic predicates, and counts beyond six remain
fail-closed. Optional single-target wording is supported by the typed owner but
excluded from the conservative measured plural cohort.

## Removal condition

Retire these operation names only if a successor preserves the same closed
source grammar, shared cardinality and owner relation, exact target identity,
partial-illegality behavior, canonical simultaneous transactions, scalar
tap-state replacements, capability closure, privacy, rollback, and replay
without runtime Oracle interpretation or family-specific applicability checks.
