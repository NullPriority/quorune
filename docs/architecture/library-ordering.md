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
top of the instructed player's library, reveal those identities only to the
instructed viewer and the analyst record, and issue a strict schema. The response is
revalidated against the exact looked-at identities before any library order is
changed.

When a selected target comprises multiple physical cards, its owner's
library end applies to the entire composed target. The ordinary physical
ordering choice uses the original owner's completed instruction and the
current composition. The owner chooses the order privately before any
part moves.

## Saved artifact search choices

The reviewed artifact search with a resolution payment orders private candidate
references deterministically. Its saved semantic program identifies the ordering
contract. Historical programs retain their original candidate ordering and
resolution behavior.

The current saved program applies the canonical library search restrictions
after a successful sacrifice. A search limited to the top of the library
offers and accepts candidates only within that scope. The resolving player
may fail to find and still shuffles. Search prohibition skips the private
choice and preserves the required shuffle.

The source-spanned artifact exchange retains the paid permanent's battlefield
mana value and the selected library incarnation across the resolution payment.
The result stays private until the existing zone movement makes it public.
The exact grammar's separate final shuffle is applied when the initial
sacrifice is impossible, based on CR 608.2c and 609.3. This reading does not
change the stored programs' zero-shuffle behavior.

## Targeted top-half exile

A self-cast trigger targets one opponent through normal trigger placement.
On resolution the top-exile owner computes half of that opponent's current
library size, rounded up, and moves that many current top cards face up in one
zone transaction. It grants no play permission. The library size determines
the quantity inside the authoritative transaction, with no player search or
advance reveal.

## Fixed targeted inspection

`library.inspect.fixed_target_player` represents the resolving controller
privately looking at a fixed one, two or three cards of one targeted player's
library. The targeted owner and inspecting actor are separate typed roles.
The ordinary target owner enforces any opponent restriction and revalidates
the announced target before inspection. Only the actor receives the looked-at
identities and private decision; a short library supplies its available cards.

The new `inspect_target_library` operation pins the current top order and each
logical incarnation. Its closed version-one descriptor supports an unchanged
look, optionally moving the single top card to its owner's graveyard, or
choosing exactly one inspected card for its owner's graveyard or exile.
Completion rejects changed order, foreign references and changed incarnations
before mutation. Revealing knowledge and moving the chosen card delegate to
`RevealLibraryCardsIntent` and the canonical destination-replacement zone owner.
These actions do not search, mill, shuffle or reorder the remaining library.
An independently represented next-turn draw retains its original controller
and delayed-trigger owner.

Saved continuations retain the explicit operation, descriptor version, viewer,
target and inspected identities. Historical library operations keep their
existing codecs and replay meaning. Multiple-card ordering or destination
partitions, conditional payments, optional initial inspection and face-down
play permissions remain outside this grammar.

## Private top-four exile and casting

The resolving controller privately inspects up to four cards from the
targeted opponent's library and chooses one. The canonical move attempts
face-down exile, then the unselected cards go to the bottom in deterministic
random order. The continuation retains the original top order and
incarnations through replacement choices and replay.

A successful exile grants the resolving controller private viewing and
spell casting for that exile incarnation. Normal timing and costs still
apply. The cast binds the validated any-type mana permission before the
card is announced, and the payment owner keeps actual mana provenance for
restrictions and snow costs.

## Face-down library entry

The Cloak instruction uses the existing face-down entry and turn-up owners. A
cloaked permanent enters as a face-down 2/2 creature with Ward {2}. Its marker
preserves the method through save and replay. A legal printed-cost turn-up
uses the physical card's front characteristics and the ordinary mana and life
payment authorities. A separate printed turn-up method can still be offered.

The fixed top-five instruction lets its controller look at the available top
cards and choose exactly two when available. A lone card is the only choice.
The response
specifies the order of the selected entries. The continuation pins all looked
incarnations and delegates the entries to the zone owner one at a time. The
remaining cards go to the bottom in deterministic random order. The choice,
incarnations, and bottom ordering stay private.

## Search queries and player roles

The canonical library-search query accepts a library owner and a separate
searching player. Restrictions and bounded top windows belong to the searching
player; the returned card identities belong to the instructed library. Existing
self-search callers keep their original default. The query is read-only and
does not itself issue a choice, grant visibility, move a card or shuffle.

A version-nine target-library exile search keeps both roles explicit through
choice, validation and movement. Its mandatory unqualified count requires the
full available quantity. The searching actor receives the private choice; the
target library owner shuffles after canonical simultaneous exile and any
destination replacements. Search prohibition still preserves the printed shuffle.
Saved identities reject changed incarnations, and older search versions cannot
carry this foreign-library authority. Contextual event-player clauses, qualified
and optional counts, and unrepresented destinations remain separate boundaries.

A printed pair can link an optional private artifact search to a later
optional death return. Declining the search leaves the library order in
place. A search can find zero cards and still shuffle. The search exposes
only the actual face-up move and binds its exile incarnation to the original
source. The death trigger can return all still-current linked cards to
their owners' hands, including cards from multiple entry triggers. An
entry that resolves after an earlier death keeps its original link.

## Single land entry during search

A direct library search that puts one land onto the battlefield can pause for
the affected player's entry replacement choice. The private continuation
retains the chosen land and its entry life payment response. Entry and any
payment complete through the existing zone and life owners. Once the land
enters, the search records its result, shuffles, and resumes the resolving
spell or ability. A fetch activation keeps its paid activation cost when
the entry choice suspends the search. The continuation rechecks the selected
library object's incarnation before placement. Invalid responses preserve the
pending choice and its transaction state.

A fixed basic-land search can give the announced target player the private
choice in their own library. A selected basic land enters tapped under that
player’s control. The target may finish the search without finding a card.
The target shuffles their library in either case, after the entry and any
replacement choices finish. A saved continuation keeps the original payer
and the searching player bound to their roles.

## Opponent top exile and public land entry

A self-cast trigger that exiles four cards from one targeted opponent's library
uses the fixed library-top batch owner. Only the available cards move, and
the instruction finishes its destination replacements before the next choice.

The resolving controller can then choose any number of face-up land cards owned
by that opponent from the public exile zone. The choice includes lands exiled by
earlier instructions and pins the offered incarnations through save and replay.
The selected lands enter together under the resolving controller through the
ordinary battlefield-entry replacements. Optional entry life payments belong to
that controller, are chosen before the batch moves, and share the controller's
available life. This instruction does not spend a land play.

## Playing a revealed matching card

After a matching card is publicly revealed during Kinship, the resolving
controller can play that same library incarnation. Cast options use the
ordinary resolving cast proposal and leave additional costs in force. Land
play uses the current turn and land quota, including ordinary entry
replacement choices. The offer, selected card, and originating trigger
remain bound through save and replay.

## Revealed top-card placement

A mandatory reveal can place the resolving controller's exact top card on the
battlefield when it is a land. The ordinary entry and replacement owners
handle its arrival independently of the controller's land plays. The other
result moves the revealed card directly to hand and keeps its identity public.
An empty library ends the instruction immediately. Save and replay preserve
the original reveal and the exact card through any pending entry replacement.

## Public top-card statistics after Scry

A fixed Scry can continue by publicly revealing the resulting current top card.
One rules query reads that physical card's effective library type, power and
toughness. For a creature card the resolution passes power to the ordinary
draw owner, then toughness to the life-change owner. The revealed card stays
on top until a draw or its replacement changes the library. A pending draw
replacement retains the bound life result without repeating the reveal. Only
the Scry controller sees the other inspected cards.

## Targeted owner placement

The original target is revalidated before its owner chooses a library
position. The continuation binds the original physical target and its current
incarnation along with the separate resolving controller. A successful
response uses the ordinary zone-transition owner to put that object on top
or on the bottom of its owner's library. Later instructions continue for the
original controller.

## Linked public exile and spell names

Two separately ordered controller color-cast triggers can share one linked
exile set with a static cast prohibition. Each trigger targets one player at
stack placement and reads that player's current library top on resolution.
Canonical zone movement records the resulting exile incarnation against the
original source and linked ability identity.

The current battlefield source reads its face-up linked exiles and applies the
name restriction to its controller's opponents through the ordinary cast-offer
and cast-validation owner. A split card in exile supplies both names. The
prospective spell supplies the chosen face's name, both names when fused, or no
name when cast face down. The restriction ends for a linked card when it leaves
exile, and a source that leaves and returns uses its new incarnation.

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

A represented tap activation can draw one private card and then move its
original source to the top of its owner's library. The ordinary draw completes
before the move. The move uses the source's battlefield incarnation from the
activation. If that incarnation has left, the draw still resolves.

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
handling retain their existing stack and effect owners. Variable counts and
unrepresented sibling results remain residuals.

`library.shuffle.fixed_target_player` adds one public player target through an
explicit `target_library_shuffle_version: 1` marker. The target can be the
controller or another active player; empty and singleton libraries are legal.
The existing canonical shuffle intent resets reordered-card knowledge and
records the instructed player's shuffle. Source costs, target protection and
resolution revalidation remain with their shared owners. The unmarked
controller-only payload and existing handler identity retain their replay
meaning. The executable marker independently requires the target capability
and exact single-player schema even after coverage metadata is removed.
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

## Exiling the controller's library top

One ordinary targetless activation pays a source tap and one life before it
can resolve. Resolution uses the ability controller's current library top
and moves that card face up through the existing top-exile and zone owners.
An empty library produces no movement. The activation grants no permission
to play the exiled card. Static top look and cast permissions keep their own
visibility and casting rules. The qualified artifact-or-colorless spell
permission checks a proposed face-down spell before offering it, when
accepting the cast and when validating its ordinary payment. Legacy typed
permissions keep their recorded front-view behavior.

## Random graveyard bottom

An optional player-targeted instruction takes the chosen player's complete
current graveyard. The simultaneous zone owner processes destination
replacements before placing actual library arrivals as a randomly ordered
bottom group. Existing library cards keep their relative order. The seed,
affected seat and resolving stack reference fix the permutation across a
pending replacement and exact replay. The random order is internal to the
zone transaction and never grants positional knowledge of a multi-card
group. The archived, unversioned graveyard-bottom operation remains available
to stored programs for historical replay.

## Shuffle and play the exiled top card

An untargeted five-mana activation shuffles its controller's library and then
exiles the current top card face up. Only the card that actually reaches exile
gets the current-turn permission. A land uses ordinary land timing and the
player's land allotment. A spell uses the free mana cost as its one alternative
cost and still pays applicable additional costs. Printed X is zero. The
permission expires this turn and the unused card stays in exile.

## Opposing land entry and top exile

The committed land-entry controller supplies up to two cards from the
current top of their library for face-up exile through the simultaneous zone
transition owner. The untargeted ability keeps the original entry controller
and original ability controller through priority and replay. The following
draw uses the ordinary per-card draw transaction privately for the ability
controller. A short or empty supplying library changes only the exile count.

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

The normalized ninjutsu activation observer uses effect version 16 for a
private fixed-one hand selection and chosen bottom order. The private choice
advertises one required hand card and the bottom order. The command submits
both groups. Replay decodes the stored effect version.

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

A source-entry instruction can measure its resolving controller's current blue
devotion once, then look privately at that many available top cards. The
controller may keep one on top. The existing library selection owner puts the
rest on the bottom in random order. After placement, the instruction compares
the same measured devotion with the current library size and delegates a
qualifying win to the game-outcome owner. A zero count skips both the look and
the placement. The comparison still runs, including when the library is empty.
Win prohibitions apply at the outcome boundary, after the private ordering.
The resolved-count selection uses descriptor version 26. The private one-card
hand and ordered-bottom partition retains version 16.

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

A version-28 fixed selection publicly reveals the available cards within a
fixed top prefix and samples one eligible creature card when present. The
resolving controller and stack reference key the sample, so replay and a
pending entry replacement retain the same physical card. The controller
makes no selection or ordering choice. The usual entry owner applies
replacement effects before the remaining revealed cards move to the bottom in
random order. The public reveal records the identities without exposing the
resulting bottom order or cards beyond the sampled prefix.

Aura-capable or ambiguous selectors, attacking entry, other linked post-entry
grants and graveyard remainders remain compiler residuals. Another player's
library, conditional or payment instructions, unowned numeric, chosen and name
predicates, other exile, cast or play destinations, and unowned preceding or
trailing effects also remain material compiler residuals.

The immutable library selection intent and its commit bridge own the sealed
arrangement, battlefield attachment, and resulting library knowledge. The
runtime host delegates the selection to that bridge.

A public single-card placement commits the reveal of the controller's current
top card before selecting its printed result. The immutable continuation uses
the revealed card's physical and logical identity for entry, a graveyard move,
or an optional bottom placement. A card kept on top retains the information
already revealed, and other library cards stay private. An empty library
finishes the effect without issuing a choice.

Optional permanent entry uses the canonical non-targeting Aura attachment
rules. With no legal attachment, the controller can use the printed fallback.
When an attachment or replacement choice pauses entry, replay resumes the
sealed movement through the normal zone owner without repeating the reveal.

## Opposing-library top exile and play

A controller end-step ability can exile each active opponent’s current top card
in one simultaneous move, skipping empty libraries. Only the face-up objects
that actually reach exile receive a grant. Each grant follows its original
exile incarnation and its original trigger controller, even after the source
leaves. A card that leaves exile loses this grant when it changes incarnation.

Ordinary land-play and spell-casting query the same grant during advertisement
and validation. Timing, land limits, and other costs remain in force. A spell
cast by this grant can use existing mana as though it were any color. True
colorless requirements and restrictions on spending each mana unit still apply.
An independently active permission to spend mana as any type maintains its own
scope and duration.

## Private controller-library top exile

An untargeted resolving spell can let its controller look at the current top
two cards of their library and exile them face down together. The batch grants
look access only to that controller and only to the objects that actually
reach exile. That access stays in place for the original exile incarnation.

The ordinary land-play and spell-cast permission uses current battlefield
characteristics to check whether the original controller controls a permanent
of the required subtype. Losing the last such permanent suspends the play
permission and regaining one restores it. Land limits, timing, and spell
costs still apply. A spell already cast continues to resolve if the
controller later loses that permission.

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

A counted creature search can bind its limit to the last battlefield mana value
of the creature sacrificed to cast the spell, plus two. Its private offer and
resumed validation use the same bound when a replacement redirects the paid
creature. The search shuffles once whether a card is found, then the terminal
instruction exiles the resolving physical spell. Copies retain the paid value
and resolve the search for their controller.

## Searches with a public query maximum

Version-eight basic-land searches evaluate an existing closed public quantity
once when their instruction starts. The original resolving controller and exact
instruction pointer bind the value in the stack's declared-public-amount cache.
The private offer and command validation use the same frozen nonnegative
maximum, including zero and values above the fixed-search grammar's limit.

`QueryBoundLibrarySearchSpec` keeps an unresolved compiler descriptor separate
from saved resolved continuation data. Only controlled lands, tapped creatures,
Shrines, distinct permanent colors and creatures directly attacking the
controller are accepted. Counted lands enter the battlefield tapped together,
or are explicitly revealed into hand, before one whole-library shuffle.
Searching for zero still searches and shuffles. Existing search windows,
prohibitions, chooser privacy, entry replacements and rollback remain canonical.
Historical search versions retain their codecs and meanings. See
[ADR 0171](../adr/0171-query-bounded-library-search.md).

## Creature searches with a power or toughness limit

The qualified search can find one creature card with current library power or
toughness of two or less. The query uses the card's library face and evaluates
characteristic-defining abilities that apply there. The controller may find
zero cards. A found card is revealed before it moves to hand, and the
controller shuffles once when the search completes.

When the trigger offers an optional search, the search and shuffle happen
only if the controller accepts. An accepted search that finds zero cards
still shuffles.

## Counted searches with alternative qualities

Different qualifications can share one counted search. Each alternative
retains its own predicate, and the search owner lists each eligible library
object once even when it matches several branches. A shared type or color
list retains its common qualifiers. The player makes one private selection
within the printed count. Tapped land results move as one batch and the
search shuffles once.

The version-three selector carries the separate branches. The original
version-two form retains its single predicate.

## Searches with conditional placement

A version-four single-card search reveals the selected permanent card and
chooses between the battlefield and hand when placement is reached. The
typed predicate reads the selected card's mana value or type, or a supported
current public-state condition relative to the original searching player.
Changing control of the source does not change that player. A false placement
condition still permits the search and puts a found card into hand.

The existing search owner validates the private selection and materializes
the destination before entering its shared zone transaction. A replacement
choice resumes that materialized result. Reveal, tapped entry, shuffle,
rollback and replay retain their existing owners. Declining an optional
search does not shuffle; searching for a stated quality and finding nothing
does. Other placement branches and unsupported condition bindings remain
explicit compiler boundaries.

## Ordered search allocations

Version-five fixed searches assign found cards in response order to two
printed destinations. The first group takes as many as its printed count
allows, followed by the remainder group. A short search cannot redirect the
first group's cards into the remainder destination. The ordered choice form
labels the groups and uses the existing reorder controls.

Both groups move through one simultaneous zone transaction, with tappedness
and entry control specified for each entering object. Replacement resumption
retains the original ordered selection; completion shuffles once before any
following instruction. Revealed hand cards remain known. An unqualified
private search keeps its hand result private while ordinary graveyard
movement exposes only the graveyard result.

Target-creature name searches retain the current legal target's name set
and battlefield incarnation when the private search opens. The existing
search window bounds eligible library cards. Bifurcate can put one matching
permanent card onto the battlefield. Pack Hunt reveals up to three matching
cards before they enter hand. A legal search can find zero matching cards
and still shuffles through the ordinary search owner.

An optional entry trigger can search for any number of cards with one
literal printed name. Declining opens no search. Accepting uses the existing
private search window, with a maximum equal to its eligible cards. The chosen
cards are revealed and moved to hand through the simultaneous result owner,
then the full library is shuffled once, including when the player finds zero.
Version 11 derives the same eligibility limit for the offer and command. A
resumed result checks its retained selection against the same private window.
Earlier search versions retain their codecs.

Creature-subtype top searches admit creature and kindred cards from the
controller's private search window. Version 12 records the selected references
in top-to-bottom order. The owner reveals the selected set in an independent
public order, shuffles the remainder once, and places the same card objects
on top without a zone change. Only the controller learns the permutation.
Earlier single-result library placement retains its existing code and format.

The hidden search owner validates each versioned search descriptor through
the same checker before advertising or restoring the search. The checker
decodes search roles, selectors and saved input identities. The search
owner retains authorization, projection and movement.

## Creature searches with a total mana value limit

A fixed total mana value limit applies to the complete subset of creature cards
found in the controller's library. The private choice offers each individually
eligible card once and the search owner validates the selected subset against
the same bound before movement. Mana value is read in the library, where X in
a mana cost is zero. The controller may find zero cards and still shuffles.

The zone owner processes the selected cards as one simultaneous battlefield
entry batch and handles any entry replacements before commitment. The search
shuffles once after the batch. The choice and the remaining library order stay
private through replacement pauses, saves and replay.
[Protean Hulk rulings](https://magic.wizards.com/en/news/feature/ravnica-remastered-release-notes)

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

New Game Record checkpoints carry fetch entry placement version 1. The built-in
search commits the entry setting from its trusted one-card placement instruction
and binds it to the saved search decision. The zone transition evaluates entry
effects using that setting. Historical records without the marker replay with
their recorded placement behavior.

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

A spell that stops at the first creature can use that public reveal to
trigger a separate counter placement. The trigger retains the creature's
revealed mana value. An X symbol in the creature's cost contributes zero
while the card is in the library. The controller chooses one creature they
control after the spell finishes, through ordinary target selection and
resolution. A scan without a matching creature creates no trigger.

Other players' libraries, variable or chosen stopping conditions, other
linked revealed-count or mana-value results, casting results,
shuffle-the-rest forms and unrepresented destinations remain outside
this scan family. Fixed-count selection and Discover retain their
separate contracts.

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

## Whole hand and graveyard recycling

The existing recycling instruction has an explicit inner version two for a
complete hand plus graveyard collection of the resolving controller or all
active players. The selector validates every owner and zone before committing
one canonical simultaneous movement batch. Commander destination choices suspend
that entire batch; no library shuffles until all replacements finish. Hidden
hand identities and private library order remain server state. Every designated
library shuffles once, including empty collections.

The complete reset composes with independently closed drawing in printed order:
every library shuffles before the active player draws, followed by other players
in turn order. Draw amounts, replacements and restrictions retain the canonical
draw owner. The resolving spell stays on the stack during its own reset and
finishes at its represented destination. Assist, Flashback, modal and Entwine
prices retain their existing owners. See
[ADR 0169](../adr/0169-hand-graveyard-library-reset.md).

## Whole-graveyard recycling

A closed recycling instruction binds the resolving controller, one declared
player target, or all current players. It snapshots public graveyards and
delegates the complete batch to the canonical simultaneous zone owner. All
destination replacement choices complete before each designated library is
shuffled once, including empty graveyards and empty or one-card libraries.

The resolving spell enters its graveyard after the instruction completes.
Printed activation prices remain paid when a player target becomes illegal;
revalidation also prevents any following draw from resolving alone. The
shuffle owner clears order knowledge and preserves seeded replay. Selective
card sets, hidden-zone wheels and unrepresented siblings remain unsupported.

## Private Bargain search

The search owner shuffles after the hidden library move and before offering
the resolving cast. A pending destination replacement retains the search
choice and its actor until resumption. If the selected card stays hidden,
the replacement journal is visible to the searching player and the analyst.
A replacement that moves the card to a public zone uses the public journal.

## Target permanent shuffle and top-card reveal

A legal target permanent moves through the canonical zone-transition owner. Its owner is captured before movement and shuffles their current library after the move, including when a replacement redirects it. Required component ordering stays private to that owner and resumes before shuffling. Only the resulting top object is revealed. A physical permanent card can enter under the library owner’s control through the normal entry and attachment owners. Other objects remain on top until the ordinary rules move them. An illegal sole target suppresses the entire result.

## Matching card types after targeted destruction

Targeted destruction can direct the affected permanent's controller to
reveal cards from their library. A legal target that survives supplies
its current controller and card types. A target that leaves supplies its
last known ones. The library owner publicly reveals the prefix through
the first matching permanent card, puts it onto the battlefield, and
randomly bottoms the nonmatching cards in the inspected prefix. The
ordinary library entry owner handles replacements and Aura attachments.
The public event retains the revealed identities without publishing the
resulting bottom order.

## Draw before opposing top exile

A closed battlefield activation can draw for the resolving controller and
then exile each current opponent's top card in the ordinary movement batch.
The draw remains private. Empty opposing libraries contribute no object,
and the resulting face-up exiles grant no play permission. The separate
Ingest trigger still takes only the damaged player's current top card.


An exact artifact search can use the effective battlefield mana value from
a committed activation sacrifice. The receipt identifies the original
sacrificed incarnation and is available after the source or payment leaves.
The resolving player may fail to find a matching artifact. They still
shuffle after a prohibited search or an unsuccessful battlefield entry.
The result uses the ordinary entry replacement owner. Search publication
uses the actual destinations of the selected cards.

## Reveal until the announced number of lands

The cast offer limits X to the players in the game. Once announced, X stays
with the resolving spell or copy. The existing public prefix scan collects
that many lands or reaches the end of the library. It puts the matched lands
onto the battlefield tapped and randomizes the other revealed cards on the
bottom. Choosing zero leaves the library untouched.

## Basic land pair with a shared subtype

This counted library search permits zero, one, or two basic land cards.
Two choices must share a land subtype. A subtype-free basic land can be
chosen alone. The actor's private search choice describes the pair constraint,
and the canonical result owner checks it again on the current cards.
The two entries share one tapped movement event, followed by the printed
shuffle. The same forced shuffle runs when the search is prohibited or finds
no cards.
