---
title: "Browser product"
status: "current"
authoritative_source: "web client, projected protocol, server application, and headless browser tests"
verified: "2026-08-25"
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

Players submit a public deck URL or pasted Commander list. Validation resolves
the list against the pinned local snapshot, reports owner-private issues, and
requires every configured seat to be ready before the owner can start. Players
may clear only their own deck/readiness. The owner can rotate the invite,
remove a nonowner before start, or replace an unstarted room.

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

Mode checkboxes maintain the server-issued printed order regardless of click
order. Adding or removing a mode preserves targets for every still-selected
mode and drops only groups that are no longer legal.

The ordered Scry form labels every looked-at card from the seat-private
projection. It separates top and bottom groups, states which end of each list
is nearest the top or bottom of the library, and provides card-specific native
buttons for keyboard and screen-reader reordering. Other seats never receive
the looked-at identities or partition.

## Mana, priority, and turn controls

Automatic mana selects from authoritative mana abilities and routine payment;
manual mana lets the player choose the activation order and exact issued modes.
Only an unchanged pure tap-for-mana activation can be undone before spending or
passing. Costs with sacrifice, life payment, restrictions, or other side
effects are not locally reversible.

Automatic pass submits only an ordinary pass-only capability. It stops for a
meaningful nonmana action or player choice. Full control waits for the player to
press the pass control. Empty-stack main phases label the intentional advance,
and the read-only phase rail distinguishes active player, priority holder, and
current step without submitting commands itself.

Combat forms consume only the current legal assignment maps. Concession uses a
true-only confirmation on an active player decision. A completed game removes
all action controls and restores the same winner or draw after restart.

## Inspect, stop, and resume

Every game member can inspect safe lifecycle metadata. The view contains no
record path, checkpoint, private zone, capability, or analyst artifact. Only
the room owner can request an administrative stop or resume. The stop is
durable and disables actions for every seat; resume returns to the preserved
decision after refresh or process restart.

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
