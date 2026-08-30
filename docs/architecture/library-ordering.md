---
title: "Library inspection and ordering"
status: "current"
authoritative_source: "typed library-choice and mutation owners"
verified: "2026-08-09"
audience: "rules, compiler, replay, and browser maintainers"
maintenance: "hand-maintained"
---

# Library inspection and ordering

Quorune represents private library inspection as a typed choice followed by a
separate authoritative mutation. A choice handler may read only the projected
top of the instructed player's library, reveal those identities only to that
player and the analyst record, and issue a strict schema. The response is
revalidated against the exact looked-at identities before any library order is
changed.

## Shared ordered partition

`LibraryPartitionChoice` is the one private complete-partition schema for
looked-at library cards. The server names the two destinations and their order
semantics; the browser renders those issued destinations without deriving a
card-specific choice. Scry supplies `top` and `bottom`, while Surveil supplies
`top` and `graveyard`.

`OrderedLibraryPartition` validates that every looked-at reference occurs
exactly once across the two groups. Operation-specific arrangements retain
their own rules and commit owners after this shared schema boundary.

## Fixed Scry

`library.scry.fixed_controller` owns one positive fixed-count Scry instruction
for its controller. `LibraryPartitionChoice` requires a complete, duplicate-free
partition of every looked-at card into:

- `top`, ordered from the new top downward; and
- `bottom`, ordered from the new bottom upward.

`ScryArrangement` freezes that response. `commit_scry_arrangement` verifies that
the physical cards are still the current library top, then commits the complete
arrangement with one list mutation. The public event reveals only counts; card
identities and the resulting hidden order remain seat-scoped. Scry 0 and Scry
with an empty library create no Scry event.

The schema retains the historical `destination: library_bottom` hint and the
handler accepts the former bottom-subset response for Game Record v3 command
replay. New clients should use the ordered partition. Runtime code does not
parse Oracle text, and ordinary top-card reordering remains a separate typed
operation rather than a second Scry implementation.

## Fixed Surveil

`library.surveil.fixed_controller` owns one mandatory positive fixed-count
Surveil instruction for its controller. The private continuation pins every
looked-at physical and logical object identity. `SurveilArrangement` requires a
complete `top` and `graveyard` partition, including the order of both groups.

`commit_surveil_arrangement` revalidates the exact current library top, commits
selected cards through the canonical simultaneous destination-replacement and
zone-transition owner, then orders the retained cards on top. The public result
names only cards whose actual destination is public; retained identities and
library order stay private. The owner emits `player.surveilled` after the whole
process, including when a positive instruction finds an empty library, and
replacement choices resume through the ordinary semantic-intent continuation.

## Fixed library selection

`library.select.fixed_controller` owns one complete fixed positive inspection
or reveal of the resolving controller's library top. The compiler supplies a
closed choice policy, typed characteristic predicates, hand cardinality, and a
graveyard or library-bottom remainder destination. It may request one complete
`LibraryPartitionChoice`, or a bounded object choice when the printed remainder
uses random order.

`LibrarySelectionArrangement` pins every looked-at physical and logical object
identity and requires an exhaustive `selected_refs` and `remainder_refs`
partition. Commit revalidates the unchanged library top before mutation,
moves the selected hand cards and any graveyard remainder through the canonical
simultaneous destination-replacement transaction, and delegates bottom order
to the shared ordered-partition mutation owner. A replacement choice suspends
before any member of the partition moves. Public reveals project the known top
to every seat; private looks and hidden resulting order remain seat-scoped.
Exact replay carries the immutable arrangement and any replacement selections.

The family excludes variable counts, another player's library, conditional or
payment instructions, numeric, chosen, and name predicates, battlefield,
exile, cast, or play destinations, and any unowned preceding or trailing
effect. These exclusions remain material compiler residuals rather than
runtime prose interpretation.

## Deliberate boundary

The current family excludes simultaneous instructions for multiple players,
dynamic counts, effects that add cards while Scrying, Scry-trigger compilation,
fateseal, and other non-Scry library ordering. Fixed Surveil separately excludes
zero, dynamic, optional, cost, targeted, repeated, copied, and granted forms;
additional looked-at cards; linked result consumers; and Surveil-event consumer
grammar. In particular, CR 701.22c requires a future APNAP decision coordinator
and simultaneous commit; a normal four-player game in which one player Scrying
has opponents is not that case.
