---
title: "Drawing-card transaction"
status: "current"
authoritative_source: "quorune/drawing, semantic_runtime/draw_replacements.py, semantic_runtime/draw_reveals.py, and CR 121 contract/conformance artifacts"
verified: "2026-08-06"
audience: "rules, compiler, replay, and engine contributors"
maintenance: "hand-maintained"
---

# Drawing-card transaction

Represented game draws use one typed boundary. `drawing/model.py` distinguishes
an instruction to draw N cards from each individual draw event, validates the
replacement or prohibited result, and pins the affected player, library size,
reason, and visibility. `drawing/restrictions.py` owns immutable per-player
permission derived from turn draw history and live fixed restrictions.
`drawing/transaction.py` is the narrow mutation owner for ordinary top-library-
to-hand draws, prohibited and empty-library attempts, draw history/events, the
represented Dredge mill-and-return result, and closed actions tied to the exact
ordinarily drawn object.

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

The current typed replacement vocabulary includes `PreventDraw`, `DredgeDraw`,
legacy fixed instruction-count `MultiplyAmount`, and `CreateResultDraws`.
Current unconditional controller-doubling programs replace one draw with two
new draw events. The producing effect is canonically excluded from its results
under CR 614.5, while another applicable doubler may replace each new draw;
result draws finish before the unreplaced remainder of the original
instruction. Historical Game Record v3 instruction-multiplier descriptors keep
an explicit compatibility handler and are not silently reinterpreted.
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
