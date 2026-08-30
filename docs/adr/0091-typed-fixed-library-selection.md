---
title: "ADR 0091: typed fixed library selection"
status: "ADR"
authoritative_source: "fixed library-selection compiler, semantic choice handler, immutable partition, and canonical zone-transition owner"
verified: "2026-08-29"
audience: "rules, compiler, runtime, browser, privacy, replay, and architecture contributors"
maintenance: "hand-maintained"
adr_id: "0091"
decision_status: "accepted"
date: "2026-08-29"
---

# ADR 0091: typed fixed library selection

## Context

The pinned Commander corpus repeats complete instructions that inspect or
reveal a fixed number of cards from the resolving controller's library, select
a bounded characteristic-matching subset into hand, and put every other card
into the graveyard or on the library bottom. The grammar appears in spells,
triggered abilities, activated abilities, and modal branches. Scry and Surveil
already supply a private complete-partition schema and an identity-pinned
ordering boundary, while the canonical zone-transition owner supplies
simultaneous destination replacement.

This family is not equivalent to either named action. Selection may be fixed,
up to a bound, all matching, or split across characteristic slots; some source
text publicly reveals all looked-at cards or only the chosen cards; and a
bottom remainder may use chosen or random order. Runtime prose parsing,
individual zone moves, or a client-derived selector would fragment authority
across compiler, hidden-information, replacement, ordering, and replay layers.

## Decision

Add one source-spanned `fixed_library_selection` semantic operation with the
capability `library.select.fixed_controller`. The compiler accepts only a
positive fixed own-library top count, a closed hand-selection policy expressed
through typed characteristic predicates, and one exhaustive graveyard or
library-bottom remainder. The leaf composes through existing spell, triggered,
activated, and modal owners.

`FixedLibrarySelectionChoiceHandler` snapshots the looked-at card references
plus physical and logical identities and computes legal characteristic groups
from the typed descriptor. Chosen remainder ordering uses the existing
`LibraryPartitionChoice`; random bottom ordering exposes only the bounded hand
selection. Every response becomes an immutable `LibrarySelectionArrangement`
that exhaustively partitions the snapshot.

`LibrarySelectionIntent` participates in the ordinary semantic replacement
continuation codec. Commit first revalidates the exact library top, then moves
hand and graveyard results through one canonical simultaneous
`ZoneTransitionOwner` transaction and applies any library-bottom result through
the shared ordered-partition mutation owner. Seeded random order is derived
from stable game and source identity. Public reveals project known top cards to
all seats, while private looks, unselected identities, and hidden order remain
seat-scoped.

Variable counts, target or opponent libraries, conditional and payment
instructions, chosen, numeric, and name predicates, battlefield, exile, cast,
and play destinations, and effects before or after the complete instruction
remain material residuals.

## Alternatives

- Extend Scry or Surveil with selection-policy and destination flags. Rejected
  because those named actions have distinct rules events and fixed partition
  semantics that this ordinary instruction does not share.
- Move selected and remainder cards one at a time. Rejected because destination
  replacement preparation must complete before the first mutation and the
  printed remainder is one ordered partition.
- Let the browser infer characteristic eligibility from card text. Rejected
  because the server-issued typed predicate and choice schema are the legality
  authority and the client receives only principal-projected data.
- Admit all look-at-library grammar behind one permissive operation. Rejected
  because variable quantities, other-library access, linked results, and
  additional destinations require different typed owners and interaction
  evidence.

## Consequences

- One grammar family promotes complete cards across several existing compiler
  contexts without card-name or Oracle-ID dispatch.
- The new semantic operation, capability shape, choice handler, immutable
  arrangement, replacement continuation, and mutation path are independently
  fail closed.
- Public top-card projection now follows card visibility for every player
  library, so a public reveal is visible to opponents while a private look is
  unchanged.
- Compiler, positive and negative grammar, malformed and stale responses,
  replacement suspension, rollback, multiplayer privacy, replay, capability,
  and focused mutation evidence bind the represented boundary.
- This decision adds neither a family-specific layer-6 ability-presence query
  nor a dynamic characteristic count. Those cross-cutting boundaries remain
  unchanged.

## Removal condition

Retire this operation only when a successor preserves exact source-spanned
grammar, typed selection predicates, immutable physical and logical identities,
complete partition validation, simultaneous destination replacement, shared
bottom ordering, seeded randomness, principal projection, rollback, capability
closure, and exact replay without runtime Oracle interpretation.
