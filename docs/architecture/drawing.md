---
title: "Drawing-card transaction"
status: "current"
authoritative_source: "quorune/drawing, semantic_runtime/draw_replacements.py, semantic_runtime/draw_reveals.py, and CR 121 contract/conformance artifacts"
verified: "2026-08-06"
audience: "rules, compiler, replay, and engine contributors"
maintenance: "hand-maintained"
---

# Drawing-card transaction

A scheduled upkeep trigger can draw privately for that upkeep's player from
the original source's named +1/+1 counter count. Resolution reads the current
incarnation while available and uses its captured last-known count after
departure. A positive count proceeds through the ordinary draw coordinator.

An entering creature's controller can be offered a private draw when that
creature has strictly greater power than every other current battlefield
creature. Resolution uses the original entrant's current power and controller
or their last-known values at departure. The optional draw belongs to that
player even when another player controls the resolving trigger.

A qualified creature combat-damage occurrence can offer its damage dealer's
current or departure controller a one-life draw payment. The life intent
commits first. The original chooser receives one private draw through the
ordinary draw transaction and its restrictions and replacements.

Represented game draws use one typed boundary. `drawing/model.py` distinguishes
an instruction to draw N cards from each individual draw event, validates the
replacement or prohibited result, and pins the affected player, library size,
reason, and visibility. `drawing/restrictions.py` owns immutable per-player
permission derived from turn draw history and live fixed restrictions.
`drawing/transaction.py` is the narrow mutation owner for ordinary top-library-
to-hand draws, prohibited and empty-library attempts, draw history/events, the
represented Dredge mill-and-return result, and closed actions tied to the exact
ordinarily drawn object.

A mandatory controller replacement can turn one draw into the fixed top-two
exile result. The impulse-access owner grants ordinary play to the cards that
actually reach exile during the current turn. A short library contributes its
remaining cards. The replaced occurrence emits no draw, including when the
library is empty. The coordinator continues any remaining draws individually.

A mandatory battlefield replacement can turn its controller's individual
empty-library draw into an instruction to win the game. The replacement
removes the draw even when a restriction bars the win. A successful win
ends the game before any queued draws or remaining spell instructions.
The ordinary draw permission check and affected-player ordering still govern
whether the replacement can apply.

`drawing/miracle_model.py` defines the staged fixed-mana declaration and
distinct prospective-library and revealed-hand values. A committed draw
changes the logical object identity. The drawer records draw provenance and
does not determine a copied trigger's controller. These values grant no
casting authority. The fixed ordinary-mana lifecycle discovers one complete
current source, suspends the enclosing draw for its private reveal choice,
and casts only during the linked trigger's resolution. Variable or complex
costs, granted or duplicated declarations and unrepresented faces retain their
material residuals.

`drawing/miracle_continuation.py` separately models an intrinsic reveal decision
with the original draw request, replacement journal, instruction tail, and
previous battlefield-source reveal choices. It reconstructs the ordinary draw
through the existing replacement owner. Its closed schema cannot be decoded as
a battlefield-source reveal continuation and grants no gameplay permission.

`drawing/miracle_discovery.py` queries the complete current source program and
the actual per-player turn draw history. The intrinsic descriptor belongs to
the existing draw-reveal component registry. Both linked abilities require
complete-card admission; recognizing the descriptor alone cannot admit a card.

`drawing/miracle_coordination.py` suspends only an ordinary first draw after
existing battlefield-source choices. It retains instruction-tail actions
separately from that card's selected reveal policies. The draw transaction
revalidates the opportunity, reveals before movement, and dispatches the linked
event with the new hand identity. `drawing/miracle_triggers.py` binds that
identity to the original trigger. Ongoing disclosure is a pure query over live
and pending original triggers; copies retain the cast subject without extending
the original reveal. Retirement never restores an old audience over another
source's reveal or erases historical knowledge.

`selection/fixed_resolving_cast.py` owns the shared immutable fixed-cost option
loop also used by Madness. Producer-specific source, complete-program and
resolution-window checks stay with each producer; this helper grants no casting
authority. It retains ordinary face, cost, Aura and target handling.

`drawing/miracle_casting.py` binds the exact revealed hand object to one live
resolving trigger and its current controller. The controller may differ from
the drawer when the trigger is copied. A selected cost comes from the canonical
fixed alternative and additional-cost options. The canonical cast proposal and
commit preserve target legality, restrictions, costs and normal spell priority.
The internal resolving-hand permission does not create an ordinary hand casting
method. A card that leaves and returns to hand cannot reuse the older trigger.

A controller life-gain trigger can offer a generic mana payment capped by
the amount gained in that occurrence after replacements. The resolution
choice quotes an affordable X without enumerating the range. Zero declines
the payment. The existing mana owner commits a positive X before the draw
owner issues that many private draws for the resolving controller.

After an ordinary draw commits, `player_result_events.py` derives one immutable
public occurrence with the drawer, per-player ordinal, and own-draw-
step facts. It dispatches the ordinary draw, exact second draw, and represented
draw-except-first-draw-step events in one trigger batch without exposing the
drawn object's identity. A prevented draw, an empty-library attempt, or a
replacement result that is not a draw emits no such occurrence.

Compiled second-draw triggers can match the controller, an opponent, or any
player. The ordinal belongs to the drawing player and advances only on a
committed draw. An opponent-relative return from the graveyard uses the source's
owner. Existing controller-only and reviewed programs retain their event
conditions.

`drawing/coordinator.py` iteratively drains individual events and queued
instructions, discovers trusted runtime instruction-count and individual
replacements, recomputes permission before each individual event, issues a
private affected-player choice when necessary, queues typed draw instructions
created by replacement results ahead of the unreplaced instruction tail,
collects live CR 121.9 policies only after the result remains an ordinary draw,
and resumes from the exact remaining count without growing the Python call stack. The same trampoline
continues an APNAP batch, draw-step entry, or the exact next spell instruction.
`drawing/continuation.py` owns that strict immutable Game Record v3 value.

An untargeted spell can draw for each opponent in APNAP order and then draw
for its controller based on the distinct opponents who drew. An observer follows
replacement-created draws and records only the actual opposing seats that
committed a draw. Multiple draws by one opponent contribute once. A redirected
draw for the controller, Dredge, a prohibited draw, and an empty-library attempt
contribute zero. The controller's follow-up uses normal draw permissions and
replacements. Both phases remain within the same resolving stack frame and
Game Record v3 continuation. The result carries no drawn-card identities.

The Brotherhood instruction attaches an observer to each opponent draw in
APNAP order. The draw transaction counts actual committed draws for its
original resolving stack frame, including draws produced by a replacement.
A prevented draw, empty-library attempt, or Dredge contributes zero. The
controller's private follow-up starts after those instructions finish. The
receipt retains the count without including drawn cards in stack context.

One source combat trigger can roll a single unmodified d20 for its resolving
controller and draw that many cards. The stack instruction publishes the face
before calling the ordinary draw coordinator. A pending draw replacement
retains the count and the remaining instruction, so resuming it uses the same
roll. The following instruction adds a timestamped, game-duration no-maximum
hand-size rule for that controller. Cleanup reads it alongside current static
hand-size rules, even after the creature changes control or leaves. Dice
modifiers, replacement rolls, and abilities that consume a roll occurrence
remain outside this compiled scope.

Public die results use the resolving stack reference and instruction position
as their deterministic key. A later roll at that same instruction adds its
ordinal to the key. The first roll keeps the established key and event fields.

The shared die occurrence planner keeps the original stack instruction key
for its first sample and distinguishes retained and ignored samples. The
resolving die helper publishes the existing single-result event. A fixed
d10 library activation passes its result plus one to the ordinary target
owner’s library insertion. A pending destination replacement retains the
bound position, and a shorter library places the target on the bottom.

A current post-roll ability can offer its roller a life-paid unit adjustment
after retention. The resolving stack frame seals the sample identities and
payment ledger until the choice resumes. The final signed number is announced
once, and each retained six can trigger ordinary token creation. Card counts
use zero when the final number is negative.

A fixed draw and keyword grant samples its controlled countered creatures
once as the draw instruction begins. The sampled count feeds ordinary draw
processing. The retained battlefield identities follow the continuation
through draw replacements and supply one shared-timestamp keyword effect
lasting through the current turn. A replacement that creates another
eligible creature leaves that creature outside the retained grant.

`drawing/captured_hand.py` represents the fixed draw, reveal, and optional
cast instruction. The draw transaction retains the hand incarnation produced
by each surviving original draw. Replacement-created draws carry their own
instructions. After the sequence, `drawing/captured_hand_casting.py` reveals
the retained cards together and binds the optional cast to the current
resolution. Canonical cast offers and commits preserve legal faces, targets,
additional costs, and cost replacement choices with the mana cost waived.
The reveal lasts through the cast or decline choice. Players retain the
knowledge they gained while it was public.

`drawing/reveal_coordination.py` owns battlefield reveal-policy discovery,
private reveal/decline decisions and exact source validation. Its completion
delegates card and history changes to the draw transaction, then returns to
the existing instruction trampoline. The coordinator reexports the established
entry points. [ADR 0145](../adr/0145-draw-reveal-choice-coordination.md) records
that ownership boundary; intrinsic card sources cannot use the existing
battlefield-source policy merely by changing its active zone.
Historical v3 Dredge continuations keep an explicit validated compatibility
path. That adapter revalidates the recorded source and count against the live
trusted `DredgeDraw` component; it does not reparse Oracle text or silently
promote historical data.

Turn draws, fixed resolving-effect draws, conditional opponent-cast-color
draws, optional-follow-up draws, and draw-each-player effects converge on this
coordinator. Oracle IR v36 also lowers the closed positive fixed-count
controller, target-player, target-opponent, optional, and each-player grammar
when it is printed as an ordinary activated ability. The source-spanned node
must have a fully compiled activation cost and a strict capability-shaped draw
payload. The closed activated family also includes the exact “draw and reveal;
discard it unless it is a land” sentence used by Fa'adiyah Seer and Sindbad.
The closed “you and target opponent each draw” family retains one opposing
target and sends both recipients through the existing APNAP draw batch. It
does not impose controller-first order. If the sole target becomes illegal,
the whole spell or ability fails to resolve, including the controller's draw.
Untargeted fixed draws for each opponent retain the resolving controller and
filter the existing surviving APNAP order. A single instruction giving the
controller and its opponents different fixed counts uses one shared draw batch
with a separate controller count. It never draws for the controller early just
because that count appears first in the sentence. A replacement pause retains
the remaining ordered recipients through save/load and replay. Source ownership
does not select those recipients.

A closed draw-and-life spell retains one revalidated player target for both
instructions. The draw owner receives half the target's current library size,
rounded up once before draw replacements. The subsequent life instruction
reads the same target's current life total and rounds the loss up separately.

The version-three source-sacrifice instruction used by Standstill retains the
original caster from its unqualified normalized spell-cast occurrence.
Starting the mandatory sacrifice cost permits the draw under CR 118.12,
including when a replacement redirects the destination. The original trigger
controller can sacrifice only the original controlled incarnation. The result
draws three for the original caster's surviving free-for-all opponents in APNAP
order. This uses the existing replacement and private draw continuations.
The earlier source-sacrifice instruction versions retain their stored behavior.

The version-four optional draw used by Heartwood Storyteller retains the
caster of one normalized noncreature spell. The caster's surviving opponents
choose in APNAP order and see earlier choices. Existing draw permissions
suppress an illegal offer. An empty library still permits the choice.
The typed choice continuation stores the ordered answers before any selected
draw occurs. The canonical draw owner then draws one private card for each
player who chose to draw. Earlier optional draw variants use their existing
choice payloads and results.

The version-five source attack offer gives the original defending player
the choice and the original trigger controller the draw. Acceptance queues
the private draw before the untap and combat removal. A draw replacement
resumes that same continuation after its result. The combat result looks
up the original battlefield incarnation after the draw. A departed source
still leaves the accepted draw available.

Fixed "additional card" wording preserves the stated count as an ordinary draw
instruction. Draw-step triggers remain separate from the turn-based draw under
CR 504.1: the active player draws normally before those triggers are placed,
then each trigger uses its retained recipient and the same draw coordinator.
The independent fixed-controller sequence owner also composes one positive
fixed ordinary draw with one fixed controller life change, Scry, or private
discard of one through four cards in either printed order across spell,
triggered, and activated contexts. Each instruction still enters its canonical
runtime owner; the sequence contributes only typed ordering and continuation.
Dynamic counts, compound effects outside a complete reviewed sequence, other
drawn-card actions, and ambiguous sibling instructions remain material
residuals. Setup hands and mulligan redraws are
intentionally enclosing game procedures, not CR 121 draw events. The ordinary
intent executor rejects a
`DrawCardsIntent` that has not been routed through the coordinator, preventing
a future producer from bypassing replacement, replay, or privacy handling.

An ordinary battlefield activation can schedule one fixed replacement for its
resolving controller's next draw this turn. The duration journal retains the
source and result through save and load. Applying the replacement consumes its
exact grant before resolving a fixed life gain, a fixed token, or a private
APNAP opponent discard. The ordinary draw pipeline resumes after the replacement
result and any nested choices complete. Cleanup expires elements still in the
journal.

The current typed replacement vocabulary includes `PreventDraw`, `DredgeDraw`,
legacy fixed instruction-count `MultiplyAmount`, and `CreateResultDraws`.
Current unconditional controller-doubling programs replace one draw with two
new draw events. The producing effect is canonically excluded from its results
under CR 614.5, while another applicable doubler may replace each new draw;
result draws finish before the unreplaced remainder of the original
instruction. Historical Game Record v3 instruction-multiplier descriptors keep
an explicit compatibility handler and are not silently reinterpreted.
An individual result draw can bind its own recipient. Replacement order begins
with the current affected player. Each new recipient uses their own library,
draw permissions, and available replacements. A consumed replacement identity
follows the resulting draw through consecutive choices and replay. The
empty-hand doubler checks the current hand of its source controller before
each individual draw, including each draw created by an earlier replacement.
Its current ability and eligibility are revalidated when a pending
replacement choice resumes.
The first completed draw in an opponent’s own draw step is exempt from
the corresponding static redirect. A resolving target-player redirect
stores its target and controller in the duration journal and expires in cleanup.
When the controller leaves before cleanup, the target's draw is still skipped.

A controller's optional library-category replacement applies to one draw at
a time. After the controller chooses land or nonland, the draw owner reveals
the prefix through the first match and moves the match to hand. The remaining
revealed cards go to the bottom in the chosen order before the next draw.
The current draw occurrence authorizes each choice during resolutions and turn
draws. A no-match result can order the whole library. An empty library still
permits the replacement and produces no draw attempt.

Unsupported material or noncommutative choices fail closed.
`replacement.draw.dredge.v1` is generated from `Dredge N`, is
active only for a trusted exact graveyard CardProgram, requires enough cards in
the library, and pins physical identity plus zone-change counter through the
choice. A replacement is considered even when the library is empty, and it
finishes before a multi-card sequence or later resolution instruction resumes.
Replacement-free and prevented instructions of at least 2,000 draws, an
instruction suspended after 500 events with 1,500 remaining, and a 2,000-item
zero-count batch are regression-tested without recursion. Each excess draw past
the current library is still committed as its own empty-library attempt.

`restriction.draw.maximum-per-turn.v1` discovers trusted, nonphased battlefield
sources and supports the closed any-player, opponent, and source-controller
relations at maxima zero or one. Mandatory multi-draws execute each still-legal
event, while optional draws and draw costs require their complete count to be
legal. Dredge replaces rather than performs a draw and therefore does not
consume a maximum-one allowance. An empty library is not itself a prohibition.
The seat-scoped `offer_draw` handler validates the prospective drawer both
before issuing a task and when the chooser accepts it.

The controller end-step offer collects each player's one-card choice in APNAP
order before committing the private draw batch. Later choosers can see earlier
answers. The sealed choice continuation records a restriction for each accepting opponent
after the draw batch finishes, including when a replacement redirects the
opponent's draw. During that opponent's next actual turn, the shared attack
declaration query excludes the trigger controller and permanents they currently
control. A battle's protector still determines its ordinary combat eligibility.
The restriction ends in cleanup for the recorded turn.

Activation legality and draw outcome remain separate. A fixed draw ability is
still activatable when a live effect will prohibit the eventual draw or the
library is empty. Its mana, tap, and sacrifice costs are paid first; its stack
object then resolves through the same replacement coordinator even if the
physical source has left the battlefield. Dredge can replace that draw before
resolution completes, and only the affected seat sees the replacement choice
or drawn-card identity. Table-wide activated draws use the same APNAP batch as
other each-player producers.

One closed delayed-draw producer also converges here. A resolving typed
instruction may create exactly one controller-frozen trigger for the beginning
of the next chronological turn's upkeep. The trigger stores the creation turn
sequence, matches only the immediately following turn sequence, expires if that
turn has no upkeep event, enters the ordinary APNAP batch once, and then
requests one private draw from this same coordinator. It does not inspect
Oracle text at runtime or bypass draw replacement, prohibition, event, privacy,
or replay ownership. Other delayed times, controller- or player-relative
upkeeps, optional or variable counts, and unresolved enclosing spell
composition remain outside trust.

The fixed discard replacement preserves the affected player's first actual draw in
their draw step. Its later draws ask that player to choose one card privately.
The chosen discard uses the canonical zone owner, including any discard
destination replacement. An actual discard produces one new draw with the
consumed replacement excluded. An unsuccessful discard or empty hand mills
one card. The remaining original draws resume with their already prepared
count. Save and replay retain the private choice and any nested replacement
order before the next draw or mill commits.

The represented CR 121.6c family applies a public reveal and then a conditional
discard to the exact card moved by an ordinary draw. Those actions do not apply
when that draw is replaced, and public projection retains the revealed card as
known while it remains in hand. This boundary does not evaluate hidden-zone
continuous type changes or other specifically-drawn-card actions.

The separate `action.draw.reveal-first.v1` component represents mandatory and
seat-scoped optional policies for a permanent controller's first draw of the
turn. It pins the live physical source and top-library object through the
choice. An optional task exposes that identity only to the prospective drawer;
acceptance publishes it to the table. `drawing/transaction.py` applies the
source-linked reveal while the object is still in the library, then moves it to
hand and preserves the known-hand projection. Each accepted or mandatory source
emits its own normalized `card.draw.revealed_by_source` event. The compiler
currently closes only the basic-land and creature riders that draw a card, so
Rowen and Primitive Etchings are exact while compound riders remain residual.

This boundary certifies only the reviewed passing parts of CR 121, now including
the closed CR 121.6c action family, CR 121.7 result-draw ordering, and CR 121.9
first-draw reveal boundary. Shared-team
ordering, conditional/dynamic draw-limit grammar, complete draw-as-cost
producers, broader actions tied to the drawn card, casting-process face-down
draws, wider reveal-trigger riders, and the complete Oracle replacement
grammar remain blocked in the contract and scheduler.

Primary focused assurance lives in `test_draw_transaction_model.py`,
`test_draw_transaction_commit.py`, `test_draw_continuation.py`,
`test_draw_replacement_components.py`, `test_draw_step_rules.py`,
`test_draw_restrictions.py`, `test_draw_coordinator_iteration.py`,
`test_draw_result_ordering.py`, `test_draw_reveal_as_drawn.py`,
`test_optional_draw_choices.py`,
`test_activated_draw_abilities.py`,
`test_semantic_handlers.py`, and `test_exact_zimone_closure.py`.

An untargeted resolving spell that derives its draw count from the mana value
of an owned commander uses a public object choice at resolution when more than
one eligible commander is on the battlefield or in the command zone. The same
selected value governs subsequent life loss, including when a draw is replaced.
