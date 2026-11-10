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

## Fixed top ordering and shuffle

`library.order.fixed_controller` owns a fixed private look followed by a complete
ordering of those cards on top. `FixedTopLibraryOrderSpec` has a closed controller
and positive-count schema. The new `order_library_top` choice pins both physical
references and logical incarnations, then requires an exact permutation of the
unchanged inspected top set. Short libraries use the available cards; an empty
library completes without a choice. This procedure emits no Scry occurrence.

The handler delegates to the existing private ordering lifecycle and
`ReorderLibraryTopIntent` mutation owner. Its operation and continuation are
distinct from historical `look_reorder_top`, whose saved contract is retained.
Only the resolving controller receives the inspected identities and ordering
choice. Invalid, duplicate, omitted, foreign or reincarnated references reject
before mutation, including after a pending choice is saved and loaded.

`library.shuffle.fixed_controller` independently owns an explicit controller
shuffle through the canonical `ShuffleLibraryIntent`. Ordinary optional-effect
and ordered-result composition express ordering, optional shuffle and a later
draw. A declined shuffle keeps the chosen order; applying it shuffles the whole
library before continuation. Source departure, paid activations and target-loss
handling retain their existing stack and effect owners. Variable counts,
another player's library and unrepresented sibling results remain residuals.
See [ADR 0131](../adr/0131-typed-private-library-ordering.md).

## Fixed hand to library top

A fixed put-back instruction privately offers a controller's current hand.
When it follows a draw instruction, the hand snapshot comes after all draws
and their replacements. The choice requires the printed number of cards, or
every card when fewer are available, in top-first order. An empty hand needs
no choice. Each offered reference is pinned to its current logical incarnation.

The simultaneous zone owner validates every selected card and destination
replacement before committing any movement. Cards that actually go to the
library retain the chosen relative order. Only the controller receives the
choice and its order. Opponents see ordinary hand and library counts and any
destination that becomes public. A pending private selection or destination
replacement resumes through the existing Game Record continuation.

The affected-player form accepts a fixed one-, two- or three-card instruction
for one announced player or opponent, every player, or every opponent. A
target's legal availability is independent of hand size. Printed-order
sequences keep their earlier results, including life loss against an empty
hand.

The affected seat receives the top-first choice. When multiple seats choose,
the APNAP owner keeps their selections private and pins the selected
incarnations until all choices are complete. The zone owner moves every
selected card in one batch, preserving the chosen order in each owner's
library. A required destination replacement choice suspends the batch before
any movement.
The projected choice and public event stream leave other seats' selections
and library orders private.

## Ordered graveyard targets

An instruction that puts cards from one graveyard at a library edge uses the
shared target plan for the announced count, ownership, and current incarnations.
The controller privately orders the surviving targets. An instruction that
targets cards from an opposing graveyard requires an explicit ordering
instruction for the controller and one shared card owner. Zero or one surviving
target needs no ordering decision. One selected mode can supply the target set.

The simultaneous zone transaction keeps its move order tied to target
announcement. After destination replacements and before publishing movement
facts, it places the actual arrivals at the printed edge in the chosen order.
Only the chooser sees the ordering decision and gains positional knowledge of
the arrivals when several cards reach the library. Replacement continuations
bind the private order and the announced batch so replay can resume without
changing the placement or revealing the permutation.

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

`mana_value_limit.py` provides a closed immutable limit over an already evaluated
mana value. It does not derive that value from costs paid, power or card names.
The battlefield-selection shape uses that limit separately from the public
battlefield power/toughness query contract.

`variable_library_selection_model.py` supplies a resolved-count bridge to the
existing fixed selection schema. Zero normalizes to no inspection. Positive
counts retain the printed selection policy, bound fixed cardinalities by the
inspection, and reduce two optional slots to one union when only one card is
inspected. The compiler binds an existing declared, cast-X or scalar amount;
the registered automatic preparation captures that count before delegating to
the fixed selection instruction. It performs no look or reveal itself. The
existing partition and destination owners retain privacy, physical/logical
identity validation, replacement continuation and replay authority. Unowned
amounts, another player's library, linked followups and unrepresented selected
destinations remain residual.

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

Current games clear positional knowledge for every card in a randomized
multi-card bottom remainder, including knowledge previously held by its owner.
A multi-card bottom order chosen by the owner remains private to that owner,
even when the inspected set was publicly revealed. Reordered cards cease to be
revealed. Zero or one remainder card creates no hidden permutation, so its
legitimate prior knowledge remains. Selected hand cards and cards outside the
remainder retain their independent visibility. This procedure does not shuffle
the library or advance its shuffle counter.

`library_selection_knowledge_version: 1` pins this correction independently in
the checkpoint and record format. Historical absence retains the previous
bookkeeping and serialized shape. The existing mutation and projection owners
remain authoritative; see [ADR 0156](../adr/0156-versioned-library-order-knowledge.md).

A version-two fixed selection additionally represents a private fixed look,
keeping up to one available card on top and exiling the complete remainder.
Zero kept and short or empty libraries preserve the printed procedure.
The same identity-pinned arrangement commits exiles through the canonical
zone transaction before the shared library-order owner places the retained
card. Saved intent identities explicitly record `library_top` only for this
form; historical hand-selection descriptors and intent bytes keep their
existing default. This narrow form does not authorize other exile selections.

A version-three fixed selection represents explicit creature, land or
planeswalker selection for entry under the resolving controller. It retains a
typed optional mana-value maximum and explicit tapped status. The shared zone
owner prepares and commits all selected entrants simultaneously before the
library partition owner orders the complete remainder on the bottom. Entry
replacement choices suspend the whole procedure before either group moves.
Saved intent identities carry entry status only for this destination; old hand
and top/exile identities retain their existing bytes.

A version-four fixed selection reveals the controller's top ten cards and
requires one available creature choice. The selected card enters with the
printed counters through the shared replacement-aware entry owner. The
resulting battlefield incarnation gains hexproof through the controller's
next turn boundary. The canonical whole-library shuffle follows, including
when the revealed prefix contains no creature. Pending entry choices retain
the exact revealed identities, counter instruction, and pending shuffle.

Aura-capable or ambiguous selectors, attacking entry, other linked post-entry
grants and graveyard remainders remain compiler residuals. Another player's
library, conditional or payment instructions, unowned numeric, chosen and name
predicates, other exile, cast or play destinations, and unowned preceding or
trailing effects also remain material compiler residuals.

## Discover

`library.discover.controller` represents a nonnegative controller Discover
instruction. Its immutable progress pins each public exile incarnation. The
same closed action composes inside spells, triggers and paid activations; the
captured controller persists after an activation sacrifices or exiles its source.
The existing zone-intent continuation exiles one library card at a time until the
nonland card's mana value is at most the resolved amount. The cast decision
independently checks the resulting spell's mana value against the same inclusive
limit, retaining canonical face, target, cost and prohibition queries.
Amounts may use the existing declared cast-X, public-query or scalar result
owners. A defined source quantity binds before an optional effect wraps it;
departure quantities use last-known battlefield information. The resolved amount
remains fixed throughout the scan and its choices. Zero still performs Discover's
scan; declining an optional Discover performs none.

Mandatory cost replacements suspend the whole attempted cast before payment
commits. The immutable cast intent retains the resolving permission, candidate,
face, cost and targets. Resume replays ordered choices through the canonical
payment journal inside a transaction, including when distinct cost objects need
separate choices. The enclosing instruction waits until casting completes;
saved cost choices cannot become ordinary priority-cast authority.
An immediate conditional return caused by a paid source can require another
entry choice after the cast commits. That choice retains the paid cast and the
parent's remaining work; Discover cleanup and followup wait for the return.

The shared cast-or-decline form offers only current legal options. A declined
or unavailable cast moves the discovered card into hand. The canonical zone
owner moves the other exiled cards to the bottom in random order, then the
procedure emits `player.discovered`. Applicable destination replacements suspend
cleanup before the whole bottom batch moves; the saved continuation retains its
public exile progress. Empty libraries still complete the action.
Casting and cleanup preserve the enclosing instruction continuation; the cast
spell resolves after that enclosing spell or ability finishes. Cascade retains
its separate strict-less comparison and cleanup contract.

Unowned quantity expressions, linked discovered-card references and discovery
observers remain material. Original-card admission and focused native witnesses
bound the implemented claim; a recognized action does not establish that every
source containing it is closed.

## Counted searches with an X bound

A counted library search can bind its mana value limit to the announced X on
the stack or the X retained at its source's entry. The compiler validates the
rest of the selector against the existing counted search shape. Resolution
seals the limit before it offers private choices, and resumption validates the
selection against the same limit. A stated quality still permits the player to
search and find nothing.

## Counted searches with alternative qualities

Different qualifications can share one counted search. Each alternative
retains its own predicate, and the search owner lists each eligible library
object once even when it matches several branches. A shared type or color
list retains its common qualifiers. The player makes one private selection
within the printed count. Tapped land results move as one batch and the
search shuffles once.

The version-three selector carries the separate branches. The original
version-two form retains its single predicate.

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

## Controller reveal-until partitions

A typed controller-only scan reveals a consecutive prefix through a fixed
positive number of qualifying cards. The pure query stops at the required
match and leaves all cards in the library until the printed result. Matching
hand or battlefield results delegate to the existing fixed-selection owner;
all-revealed hand results use its existing reveal and selection intents without
an invented choose-all interaction. Empty or insufficient-match libraries do
as much as possible. This procedure does not draw cards.

Mandatory matching partitions retain current identities, entry replacements,
chosen ordering and private projection. A randomized multi-card remainder
loses positional knowledge for every player; a chosen multi-card bottom order
is owner-private even after public reveal. Singleton and unaffected-card
knowledge remain valid. Random bottom ordering is not a shuffle.
`library_selection_knowledge_version: 1` independently pins that knowledge
correction while historical absence retains earlier bookkeeping. See
[ADR 0156](../adr/0156-versioned-library-order-knowledge.md) and
[ADR 0157](../adr/0157-controller-reveal-until-partitions.md).

Other players' libraries, variable or chosen stopping conditions, linked
revealed-count/mana-value consumers, casting results, shuffle-the-rest forms
and unrepresented destinations remain outside this scan family. Existing
fixed-count selection and Discover retain their separate contracts.

## Deliberate boundary

The current family excludes simultaneous instructions for multiple players,
unrepresented dynamic counts, effects that add cards while Scrying,
fateseal, and ordering outside the represented fixed controller form. Fixed Surveil separately excludes
optional, cost, targeted, repeated, copied, and granted forms;
additional looked-at cards; linked result consumers; and Surveil-event consumer
grammar outside the normalized fixed-body observer. In particular, CR 701.22c
requires a future APNAP decision coordinator
and simultaneous commit; a normal four-player game in which one player Scrying
has opponents is not that case.
