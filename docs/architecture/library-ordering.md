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
`top` and `graveyard`. Optional top/exile selection supplies `top` and `exile`
with a server-issued maximum for the top group. The browser initializes and
validates per-destination bounds when present; the server revalidates them.

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
identities and the resulting hidden order remain seat-scoped. Scry 0 creates
no choice or event. A positive instruction with an empty library completes
without a private choice and emits `player.scried` once, after the procedure.

The schema retains the historical `destination: library_bottom` hint and the
handler accepts the former bottom-subset response for Game Record v3 command
replay. New clients should use the ordered partition. Runtime code does not
parse Oracle text, and ordinary top-card reordering remains a separate typed
operation rather than a second Scry implementation.

## Mill result counts

The existing Mill preparation owner consumes a nonnegative resolved integer.
Zero snapshots no library cards and commits no zone movement or Mill event;
it does not interpret Python's empty slice as the entire library. Positive
counts retain the ordinary current-top identity validation and simultaneous,
replacement-aware zone transaction. Declared spell-cost or public-definition
amounts require their separate numeric capability, not runtime Oracle parsing.

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
Surveil 0 creates neither a choice nor an event.

Scry and Surveil counts may be resolved by the existing declared-quantity or
public-query amount capabilities before preparing the private partition. A
completed positive procedure can feed a normalized player-action observer;
controller, opponent, and global predicates retain their scope without
publishing inspected identities.
Whole-program admission also accepts a simple fixed draw observer beside a
represented Surveil instruction. An observer whose draw count depends on cards
put into a graveyard “this way” remains outside this fixed-body grammar.

## Fixed library selection

`library.select.fixed_controller` owns one complete fixed positive inspection
or reveal of the resolving controller's library top. The compiler supplies a
closed choice policy, typed characteristic predicates, hand cardinality, and a
graveyard or library-bottom remainder destination. It may request one complete
`LibraryPartitionChoice`, or a bounded object choice when the printed remainder
uses random order.

“The other” remainder is accepted when a mandatory fixed selection leaves
exactly one card from the requested inspection. It retains the same complete
partition and printed destination as “the rest.” A short library selects only
the available cards; an optional selection or inconsistent requested remainder
cardinality is not inferred from this grammar.

`LibrarySelectionArrangement` pins every looked-at physical and logical object
identity and requires an exhaustive `selected_refs` and `remainder_refs`
partition. Commit revalidates the unchanged library top before mutation,
moves the selected hand cards and any graveyard remainder through the canonical
simultaneous destination-replacement transaction, and delegates bottom order
to the shared ordered-partition mutation owner. A replacement choice suspends
before any member of the partition moves. Public reveals project the known top
to every seat; private looks and hidden resulting order remain seat-scoped.
Exact replay carries the immutable arrangement and any replacement selections.

A version-two fixed selection additionally represents a private fixed look,
keeping up to one available card on top and exiling the complete remainder.
Zero kept and short or empty libraries preserve the printed procedure.
The same identity-pinned arrangement commits exiles through the canonical
zone transaction before the shared library-order owner places the retained
card. Saved intent identities explicitly record `library_top` only for this
form; historical hand-selection descriptors and intent bytes keep their
existing default. This narrow form does not authorize other exile selections.

The family excludes variable counts, another player's library, conditional or
payment instructions, numeric, chosen, and name predicates, battlefield,
unrepresented exile, cast, or play destinations, and any unowned preceding or trailing
effect. These exclusions remain material compiler residuals rather than
runtime prose interpretation.

## Library search restrictions

`LibrarySearchRuleSpec` represents current source prohibitions and fixed
opponent search windows. The existing action-permission registry supplies
typed participation; the search owner applies it before inspecting or exposing
library candidates. A bounded search exposes its complete permitted window
privately to the searcher, separately from the cards that match the selector.
One multi-card search retains one window, and normal completion shuffles the
entire library. Other zones and non-search procedures remain independent.

A prohibited mandatory search issues no choice or private inspection and
preserves an independent shuffle instruction. An optional search cannot be
chosen when prohibited, so its contingent shuffle does not occur. Cast and
activation costs remain paid. Semantic searches and historical fetch searches
use the same constraint query and retain their existing movement owners.

Resolved turn prohibitions use a typed entry in the existing continuous-effect
journal. Its decoder, ordinary cleanup and pre-action replay preserve the
duration after the resolving spell leaves the stack. Static participation
instead follows current source ability, face, controller and phasing state.
Causal-controller restrictions, payments to ignore a source, and unrepresented
replacement forms remain explicit compiler boundaries.

## Deliberate boundary

The current family excludes simultaneous instructions for multiple players,
unrepresented dynamic counts, effects that add cards while Scrying,
fateseal, and other non-Scry library ordering. Fixed Surveil separately excludes
optional, cost, targeted, repeated, copied, and granted forms;
additional looked-at cards; linked result consumers; and Surveil-event consumer
grammar outside the normalized fixed-body observer. In particular, CR 701.22c
requires a future APNAP decision coordinator
and simultaneous commit; a normal four-player game in which one player Scrying
has opponents is not that case.
