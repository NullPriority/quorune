---
title: "Browser product"
status: "current"
authoritative_source: "web client, projected protocol, server application, and headless browser tests"
verified: "2026-11-24"
audience: "local players, spectators, and product contributors"
maintenance: "hand-maintained"
concern: "browser-product"
---

# Browser product

The Quorune browser is a projected client for two-, three-, and four-player games under
the current Commander profile. It supports invite-only rooms, validated deck
submission, seat-isolated play,
read-only spectators, public history, and restart-safe game lifecycle. It does
not contain a second timing, mana, combat, target, or card-rules engine.

## Join and start a game

A guest chooses a display name, then hosts a duel or four-seat room or joins by
invite. Each top-level browser tab receives its own HttpOnly guest binding even
when tabs share a cookie jar. A guest may claim one open seat or join watch-only
as a spectator.

Players choose a saved deck, submit a public deck URL, or paste a Commander
list. Public URLs can come from Moxfield, Archidekt, or TappedOut. A provider
may reject automated exports. Its text export can still be pasted into the
room. The editor reports Commander legality, card rules support, and match
readiness separately. When all cards pass rules verification, the match-wide
Commander rules check can still be incomplete. Affected cards are visible
only to the person who submitted the list.
For card data mismatches it saves the list as an editable draft and shows every
affected card. Open a card to see the changes in Oracle text or rulings when a
retained snapshot matches the reviewed implementation. The details distinguish
formatting edits from added, removed, or changed text. Technical details include
the diagnostic ID and the program sources.
When every player has submitted a deck and a deck needs a card data review,
the host can choose **Use earlier card data**.
The game starts with one retained snapshot if it matches the earlier
implementations and every submitted deck passes its checks against it. The
selected snapshot applies to the whole match and is pinned for replay.
A four-seat room can start with three players. Every seated player must be
ready, including the fourth player once they join. The owner can rotate the
invite, remove a nonowner before start, or replace an unstarted room.
The host can pick an occupied seat under **Starting player** or leave it on
**Random**. Under **Advanced settings**, the host can set the starting life
total for every player. The default is 40 life.

Use **Edit deck** in **Saved decks** to open a copy in a new table’s editor.
Choose **Edit visually** to filter or order cards by tag, change cards and
boards, or choose a different printing. **Find printings** loads available
editions for the selected card. Manual set and collector fields remain
available when the printing lookup is unavailable. Checking and submitting
saves the edited list. The host can use **Decks and games** from the editor to
return to saved lists and recent games.

Preview-card exceptions use an exact confirmation fingerprint and never waive
banned, missing, already-released illegal, or unsupported semantic behavior.
Other seats see only that an override exists, not the implicated private deck
entries.

## Use the table

The table renders only the current projected state:

- each player sees their own hand while opposing hands remain counts;
- graveyards, exile, command zones, battlefield objects, the stack, life, and
  commander damage use public projected data;
- card inspection follows hover, keyboard focus, or an explicit compact-layout
  dialog and falls back to projected text when art is unavailable;
- tapping, counters, attachments, combat assignments, and terminal results
  update only after an authoritative packet. The printed face rotates when a
  card is tapped, and the card’s accessibility description reports its state;
- an Equipment or Aura sits beside its attached permanent and opens in the
  card viewer on hover or focus. An attachment to a player stays on its board.
- your draws and mills appear together when they arrive in the same update.
  Draws animate from your library to your hand, and mills highlight your
  library with the count in the notice. Reduced motion keeps the notices visible.
- the complete public log is paginated from the durable record through
  spectator visibility filtering. Recent activity names publicly visible cards
  and links them to the viewer. Destruction entries show the outcome and, when
  the recorded event identifies it, the spell or nearby combat damage involved.

Highlighted cards and the action tray are two presentations of the same
server-issued action IDs. Click or drag can select an action but cannot bypass
timing, priority, cost, target, semantic, fidelity, or capability validation.
Versioned generic forms render server-issued modes, targets, ordering,
assignments, private selections, and confirmations. Unknown forms remain
unavailable rather than being guessed.

Use **All opponents** to see the opposing boards or select a seat to
give that opponent the full upper row. In **Table settings**, use
**Opponent board widths** to change how the boards share the row. Use
**Detach boards** to move selected opponents to a second window, stacked
vertically beside a copy of the stack. The second window uses your current
projection for inspection and selection from offered targets. Confirm actions
on the main table. Closing the second window returns its boards to the table. Seat buttons show eligible
targets while a target choice is open. On short or narrow screens,
**Your board** switches back to your own battlefield.

Each board separates lands, creatures, and other permanents. Opposing lands
go above their other permanents. Your lands sit below yours. A permanent
with a current Creature type appears in the creature group. Lands with the
same projected printing mana output sit together and overlap, even when their
names differ. The lanes use the available space before paging.
Repeated lands and tokens share a count that opens their individual cards.
Use the page buttons in a crowded group to browse the rest of its cards.
**Arrange** changes the order and lane of permanents on your board.
**Reset layout** restores the automatic arrangement.

**Table card size** scales the card displays together. Open **Game menu**,
then **Table settings** and **Card sizes by zone** to adjust one zone
relative to that size. Quorune remembers the selected opponent and card
sizes in this browser. Card pages recalculate as the available space changes.
**Table colors** saves the accent and background tint in this browser for
the signed-in account.

Click a permanent with an offered activated ability to use it. The picker shows
the effect and available cost information when more than one action is offered.
The decision dock highlights offered targets and payments on the table when
those objects are visible there. Choose a target and a payment separately, then
confirm the commitment. Cancel closes the draft.

When choosing targets, hover or focus an eligible player or card to see an
arrow from the source. If a target is in the graveyard or exile, the highlighted
zone opens a picker for its eligible cards. The arrow stays on the selected
target while you finish the remaining choices. Target lists group cards under
their controller’s seat label and color. The battlefield gives creatures more
space than the land and other permanent rows. Current power and toughness and
+1/+1 counters appear below the card face. Counters like lore and wish counters
appear on the face. During attack and block assignment, creature rows on both
sides show current power and toughness. Right-click a battlefield card or press
**I** while it has focus to open the full card view.

Cards with available actions use a visible outline. Mana symbols wrap onto
additional lines as needed. The command zone shows the printed cost below
the card. Any commander tax appears on its own line beneath the whole cost.
At compact widths, use the card viewer button by your hand for the full
price and rules text.

The stack opens for everyone while it has items. **Minimize** returns that
space to the battlefield and keeps the stack count in the toolbar. A new
stack-targeting draft opens the stack again. Each item has a numbered
position, with the next to resolve at the top. Select an eligible spell to
target it, then confirm your cast. Page buttons appear when the stack fills
its rail. A response's target link opens the page containing that exact
entry and that entry links back to the response. An arrow connects relevant stack items to their targets by default. Hover or
focus a stack item to trace its own targets.

Seat colors match across the battlefield, priority status, stack, and
player-specific dialogs. Seat labels identify players alongside the colors.
A player's board shows a chip for each commander that has dealt combat damage
to that player. The chip uses the commander's original owner's seat color.
Hover or focus a chip to see the commander's name.

The desktop table puts turn, phase and game controls in one header. At
narrow widths the phase controls wrap below the turn summary. The Stack
button sits beside the battlefield controls. When the stack panel, recent
activity and card viewer are closed or collapsed, the battlefield spans the
table width and the reopen controls stay above it. **Right rail width** in
**Table settings** adjusts the expanded rail on desktop. Battlefield card
faces turn 90 degrees when tapped, with their names and game state kept upright.

In narrow or very short windows, the table-wide card size slider is
available in **Table settings** in the game menu. In compact layouts the lane
labels sit beside the cards. Collapse your hand to give the battlefield
more room while using the opponent views, zones, or current decision.

Each seat’s library, graveyard, and exile appear beside the battlefield.
Hover or focus a zone for a preview, and open it to page through its cards.
The zone browser fills the available rows before paging and keeps the focused
card’s details in view. **Collapse zones** keeps the counts and links in a
smaller area. The library
view shows the library count. Top cards appear when an effect grants visibility.
Public top-card reveals show the card to every seat. Private looks show its
identity to the permitted viewer. Exile uses a card back for concealed cards
and marks a visible face-down card with its status.

When your offered actions include a card in another zone, the hand area shows
a shortcut marked with its source zone. The acting player sees the offered mana
cost on the shortcut. Select the card to choose an action or inspect its full text. The mana symbols
are bundled with the browser. [Mana by Andrew Gioia](https://mana.andrewgioia.com/icons.html)

The hand shows separate printed faces, names, and mana costs. Cards shrink
when the row fills, and page controls reach the remaining cards. Hover or focus
a card for a full-face preview. Drag the handle at the top of the hand to
resize it, or focus the handle and press the Up or Down arrow. The battlefield
uses the remaining space. Turn on
**Art-focused hand** in **Table settings** for the cropped art view.
On desktop, the zone buttons, action tray and priority controls share the
column beside the hand. At narrow widths, larger decision forms use the full
width below the hand. Phone-width layouts put the zone buttons and priority
controls across the dock so their labels fit. Hover or focus a shortened action
to read its full label. **Hand
width** and **Hand height** set the preferred size within the viewport.
Card previews use the copy’s imported printing when available. Faces printed
on the same side share the artwork for that physical side. **Table card size**
also scales the command zone. The table restores saved
sizes when opened. Choose a preset or customize the accent and background tint
in **Table colors**. Quorune saves these colors for the current guest in this
browser.

Battlefield lanes group lands by printed mana output where that data is
available. Matching unmarked lands and tokens use an overlapping stack.
Choose the count to see and act on individual cards. **Arrange** lets you
change a permanent's lane or order for your view. Equipment and Auras
appear beside their permanent with a connector. Hover or focus any attachment
to inspect it. Adjust **Battlefield lane sizes** in table settings to give
lands, creatures, or other permanents more room.

Click an eligible attacker and its defender, or drag the attacker onto the
defender. Blockers work the same way. Multiple blockers on one attacker appear
in one group. Incoming attackers show **Blocked** or **Unblocked** as you
assign blockers. Choose **Confirm no blockers** when no assignments are
made. Declared attacks draw arrows from the attackers to their defenders.
Remove an assignment or clear the draft before confirming.
Battle lanes identify the protector, and planeswalker lanes identify the
controller. Choose a protector in the decision dock when a Siege enters.

Choose spell modes in the decision dock. The modes stay in printed order,
and each target group identifies its mode. Select eligible targets on the
table for the active group. Changing modes keeps targets that are still
legal for the selected modes. Open **Spell text** to read the printed
effects when an offer identifies modes by number.

Target-dependent prices update from the server's quotes for the selected
objects, players and modes. Distinct-target prices count a repeated selection
once. An unavailable target combination or a value of X above its quoted
maximum cannot be submitted as a payable choice. For spells that allow up to
X targets, enter X and select the highlighted cards on the table. The action
tray shows the selection count and offers **Choose from list** to select
eligible cards by seat, including cards behind another board or page.
**Cast with no targets** identifies an allowed empty selection before submission.

The search picker presents the offered card copies, selection count, and
destination. **Find no card** completes a search when the rules permit an
empty result. **Decline** answers the preceding optional-effect decision.
When a fetched land can enter untapped by paying life, the payment appears
after choosing that land. A commander-zone choice names the commander and the
two possible destinations.
Imported printing details follow each card copy, including through a search.
The picker shows the set and collector number when available. If that art is
unavailable, the copy keeps its printing label and readable card details.

Ordered Scry and Surveil label the cards from the player’s private
projection. Hover or focus a choice to see its full card in the viewer.
Drag a card between destinations or use its destination
menu. Drag within a destination or use the arrow buttons to reorder.
The trays identify the next card drawn and the order at the bottom of
the library or in the graveyard. Other seats see only cards that move
to a public zone.

## Mana, priority, and turn controls

Phyrexian casting prices show the mana symbols and the life amount for
each available payment. Choose one before confirming the spell.

Automatic mana selects from authoritative mana abilities and routine payment;
manual mana lets the player choose the activation order and exact issued modes.
Choose **Use manual mana** on a prepared spell, tap mana sources, then choose
**Resume cast**. The current offer keeps still-available choices and refreshes
the spell’s payment options before commitment.
Only an unchanged pure tap-for-mana activation can be undone before spending or
passing. Costs with sacrifice, life payment, restrictions, or other side
effects are not locally reversible.

**Pass priority** or **Resolve** passes once from your seat. **Pass turn** at
the bottom right keeps passing your priority during the current turn, even
when a spell or ability is available. Required choices, including attackers
and blockers, still wait for you and end the turn yield. The turn yield also
ends when the next turn begins or you refresh. **Cancel pass turn** holds your
next priority. **Resolve stack** passes your priority while the current stack
resolves. A required choice, a new stack item, or a changed response offer
returns control to you.

Open **Priority stops** beside **Pass turn** to hold at specific steps, for a
new stack item, or for graveyard activity. Auto-pass honors these stops,
including in fast mode. A manual pass acknowledges the current
stack and graveyard activity. Choose **End step before my turn** to hold during
the end step that precedes your currently scheduled turn. Extra turns and
whole-turn skips are included in that schedule. Choose a phase on the turn
rail to edit its holds. During combat, the rail shows each step and lets you
set its stop directly. To hold only during a selected opponent’s turn,
choose that seat in **Priority stops**, then select its steps. Use **After
attackers are declared** to hold for a declared attack. Before the first
main phase, choose **Main 1** and **Advance to first main** to pass your
priority until that boundary. Required decisions and any selected earlier
holds can interrupt the advance.

Auto-pass handles routine priority, including a window with only ordinary
mana abilities, while keeping meaningful spell and land offers available. After manually passing through a window with only optional
activations, Auto-pass remembers those unchanged offers for the current turn.
Use **Auto-pass through optional activations** for all such offers, or choose
**Skip unchanged this game** for one ability in **Priority stops**. The
individual setting lasts until refresh and holds again if the offer changes.
**Full control** holds your priority at each window.

Open **Loop shortcut** to record your accepted actions until the same visible
priority window returns. Choose a finite repeat count and whether to hold
your next priority or resume your usual settings. You can also hold
immediately. Each repeated action uses a fresh offer and each other player
retains their ordinary response windows. An observed response, changed offer,
or required choice can interrupt playback.

Combat forms use the current legal assignment maps. Damage shows the assigned
and remaining amounts beside each source and its recipients. Use **Assign
remaining attackers** beside a defender to set all remaining eligible
attackers to that defender. The server still validates the complete
declaration. Concession uses a
true-only confirmation on an active player decision. The player stays at the
table as a spectator while play continues. A completed game removes
gameplay controls and preserves the result after restart. Choose **Decks and
games** to open your saved lists or recent matches. The host can choose
**Play again with these decks** to prepare a fresh table. Players can join
that table, change their deck, and start a new match. The original game remains
available in **Recent games**.

## Inspect, stop, and resume

The card viewer follows hover and focus on the table. Press **Alt** to pin the
current card and select its rules text. Press **Alt** again or choose **Unpin**
to close it. **Pop out viewer** moves inspection to a second window. Recent
activity opens by default and includes attack destinations, damage and loss
reasons. Choose **Enlarge** for more room or **Full log** for earlier events.
The list collapses when you open the viewer window. Choose **Show recent
activity** to reopen it. Your library, graveyard, and exile are next to your hand. Opponents’
hand counts are public. **Revealed hand** opens the cards the current seat is
allowed to see.

Use **Filter cards by name** to narrow the cards offered in a search. The
filter keeps your selection. Creature-type choices start with types in your
deck. Search or choose **Show all types** to find any other legal type.
**Expand choice** opens a larger panel and preserves your selections.

During resolution, choose **Pay** or **Decline** and confirm in the action tray. If you remember
a response, the browser uses it only for the matching trigger in this game
when the answer is legal. The game menu clears remembered responses.
For a simple Cascade choice, **Yes, cast** and **No, decline** each submit
directly. When casting requires additional decisions, **Yes, choose how to
cast** opens the offered targets, payments, or options. Priority windows
before and after the choice still use your priority controls.

Hover, focus, or use **Inspect** to see current power and toughness, marked
damage, counters, and the active face. Planeswalker cards show their current
loyalty on the table. Select a planeswalker to see the loyalty abilities
available in the current decision window. **Game menu** contains playtest
feedback, build details, the public log, and match controls.

Every game member can inspect safe lifecycle metadata. The view contains no
record path, checkpoint, private zone, capability, or analyst artifact. Only
the room owner can request an administrative stop or resume. The stop is
durable and disables actions for every seat; resume returns to the preserved
decision after refresh or process restart. During reconnect, the table remains
available for reading. Actions become available after the browser validates
a fresh projection. A retry after uncertain delivery uses the original
command.

Administrative resume cannot clear a rules, semantic, fidelity, abort,
corruption, or completion boundary. A stale tab that loses game access stops
reconnecting and returns to the lobby.

When a required choice uses a newer format, the browser explains the update
and keeps the action’s submit control disabled.

A pending choice with one available action opens directly in the decision
dock. This includes triggered targets, ordering cards after Scry, and damage
assignment. Minimize the choice to inspect the table and reopen it with the
selection preserved.

The card picker shows which cards you can put on the bottom after a
mulligan or discard at cleanup. During a legend choice, it distinguishes
commanders from same-name token copies. When you control several triggers
waiting to go on the stack, drag their cards or use the arrow buttons to order
them by resolution. The first and last labels describe your group. The
active player's group goes on first, then the other players' groups follow in
turn order. The last group placed resolves first.


## Card data and images

The server owns the local Oracle/rulings database and image cache. The browser
never receives a bulk export or arbitrary database query. Visible projected
cards request one constrained same-origin image route; the server accepts only
the pinned Scryfall image reference and caches the response locally. Hidden
libraries and opposing hands do not enter another seat's DOM or image request
set.

Card art is optional presentation. Projected text remains the functional
fallback, and cached bytes are ignored runtime data rather than repository or
package assets. See the [content boundary](../LEGAL_CONTENT_BOUNDARY.md).

## Current product boundary

The current client is a single-node local-development application. Production
accounts, password recovery, rate limiting, hosted operations, multi-process
game ownership, general restricted-mana allocation, every future choice schema,
and a complete accessibility audit are not implemented. Current rules and card
support is bounded by generated [rules status](../RULES_COMPLETENESS_STATUS.md)
and [compiler status](../COMPILER_COVERAGE_STATUS.md).

Use [local operations](../operations/local-app.md) to run the application,
[protocol reference](../reference/protocol.md) for client contracts, and
[visibility](../architecture/visibility.md) for privacy ownership.
