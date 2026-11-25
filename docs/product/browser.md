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

The Quorune browser is a projected client for two- or four-player games under
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
list. The editor reports Commander legality, card behavior, and match readiness
separately. It shows affected cards only to the person who submitted the list.
For a card data mismatch it saves the list as an editable draft and leaves
that seat waiting for review. The deck owner can open the affected card’s data
details to see the diagnostic ID and the two program sources. Use the Oracle
and rulings snapshot for the running release before checking the list again.
Every seat submits a list before the host can start. The owner can rotate the
invite, remove a nonowner before start, or replace an unstarted room.

Use **Edit deck** in **Saved decks** to open a copy in a new table’s editor.
Checking and submitting saves the edited list. The host can use **Decks and
games** from the editor to return to saved lists and recent games.

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
  update only after an authoritative packet;
- the complete public log is paginated from the durable record through
  spectator visibility filtering.

Highlighted cards and the action tray are two presentations of the same
server-issued action IDs. Click or drag can select an action but cannot bypass
timing, priority, cost, target, semantic, fidelity, or capability validation.
Versioned generic forms render server-issued modes, targets, ordering,
assignments, private selections, and confirmations. Unknown forms remain
unavailable rather than being guessed.

The three opponents sit above your battlefield in a four-player match. Each
board groups creatures, lands, and other permanents. Use **Arrange** to reorder
your cards within a lane. **Reset layout** restores the default order.

Click a permanent with an offered activated ability to use it. The picker shows
the effect and available cost information when more than one action is offered.
The decision dock highlights offered targets and payments on the table when
those objects are visible there. Choose a target and a payment separately, then
confirm the commitment. Cancel closes the draft.

When choosing targets, hover or focus an eligible player or card to see an
arrow from the source. If a target is in the graveyard or exile, the highlighted
zone opens a picker for its eligible cards. The arrow stays on the selected
target while you finish the remaining choices. Hover or focus an item on the
stack to see its targets. Card badges show game information and available
actions. Use **Inspect** to open a full view.

When your offered actions include a card in another zone, the hand area shows
a shortcut marked with its source zone. Those shortcuts are visible to the
acting player. Hover or focus a card to see its offered cost above its name.
Select the card to choose an action or inspect its full text. The mana symbols
are bundled with the browser. [Mana by Andrew Gioia](https://mana.andrewgioia.com/icons.html)

Click an eligible attacker and its defender, or drag the attacker onto the
defender. Blockers work the same way. Multiple blockers on one attacker appear
in one group. Remove an assignment or clear the draft before confirming.

Mode checkboxes maintain the server-issued printed order regardless of click
order. Each target group shows the mode that offers it. Adding or removing a
mode preserves targets for every still-selected mode and drops only groups
that are no longer legal.

Target-dependent prices update from the server's quotes for the selected
objects, players and modes. Distinct-target prices count a repeated selection
once. An unavailable target combination or a value of X above its quoted
maximum cannot be submitted as a payable choice.

The search picker presents the offered card copies, selection count, and
destination. **Find no card** completes a search when the rules permit an
empty result. **Decline** answers the preceding optional-effect decision.
Imported printing details follow each card copy, including through a search.
The picker shows the set and collector number when available. If that art is
unavailable, the copy keeps its printing label and readable card details.

The ordered Scry form labels every looked-at card from the seat-private
projection. It separates top and bottom groups, states which end of each list
is nearest the top or bottom of the library, and provides card-specific native
buttons for keyboard and screen-reader reordering. Other seats never receive
the looked-at identities or partition.

## Mana, priority, and turn controls

Automatic mana selects from authoritative mana abilities and routine payment;
manual mana lets the player choose the activation order and exact issued modes.
Choose **Use manual mana** on a prepared spell, tap mana sources, then choose
**Resume cast**. The current offer keeps still-available choices and refreshes
the spell’s payment options before commitment.
Only an unchanged pure tap-for-mana activation can be undone before spending or
passing. Costs with sacrifice, life payment, restrictions, or other side
effects are not locally reversible.

Pass or Resolve sends one pass from this seat. Other seats still receive
priority. Automatic pass submits only routine pass-only decisions. Full
control holds each decision for the player, including priority on their own
triggers.

Pass turn arms a yield for the current turn. It also passes empty-stack main
phases and combat priority windows with no meaningful actions. A newly arrived
stack object, an available response, or a required decision pauses the yield.
Choosing a response keeps the yield available until the turn ends. A manual pass
acknowledges the current stack. Cancel pass turn,
enabling Full control, refreshing the page, or reaching the next turn ends
the yield. Each automatic pass uses its own current seat capability and is
recorded with the current turn. The game menu can ignore specific activation
stops for that turn. Each ignored ability stays available for manual activation.
The stop returns if the ability offers different costs, targets, or choices.

Open **Combat** in the turn rail to select the steps where you want to hold
priority. **Game menu → Combat priority stops** offers the same controls in a
compact layout. A selected stop holds the next available priority for your
seat at that step, even when Auto-pass or Pass turn is active. Pass manually
to continue. Attacker and blocker stops apply after those assignments.

Combat forms consume only the current legal assignment maps. Concession uses a
true-only confirmation on an active player decision. A completed game removes
gameplay controls and preserves the result after restart. Choose **Decks and
games** to open your saved lists or recent matches. The host can choose
**Play again with these decks** to prepare a fresh table. Players can join
that table, change their deck, and start a new match. The original game remains
available in **Recent games**.

## Inspect, stop, and resume

Hover, focus, or use **Inspect** to see current power and toughness, marked
damage, counters, and the active face. **Game menu** contains playtest feedback,
build details, the public log, and match controls.

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
