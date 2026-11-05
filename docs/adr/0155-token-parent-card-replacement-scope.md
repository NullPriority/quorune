---
title: "ADR 0155: token-parent card replacement scope"
status: "ADR"
authoritative_source: "Immutable replacement events and physical departure owners"
verified: "2026-11-01"
audience: "rules, persistence and replay contributors"
maintenance: "hand-maintained"
adr_id: "0155"
decision_status: "accepted"
date: "2026-11-01"
---

# ADR 0155: token-parent card replacement scope

## Context

CR 730.3d applies a whole-permanent replacement to every physical component.
CR 730.3e makes a card-only exception when the merged permanent is a token:
physical cards follow that replacement while token components follow the
permanent's destination. Treating each component as an independent departure
would duplicate permanent deaths and could force an incorrect replacement order.

## Decision

Capture immutable physical card identities from the validated token-parent
store in the existing zone subject. Keep a scoped card result within the one
replacement event. The shared replacement owner tests card-only conditions
against that result, offers competing whole and card-only effects together, and
records a selected effect once. Whole-permanent operations update both scopes;
card-only operations update the card scope. Fixed counter leaves follow the
applicable scope and replacement source's placing player.

Use the existing physical departure batch for all component destinations and
ordering actors. Schema-four resolving order requests retain the closed batch
and previously collected permutations. Each group belongs to its owner or
exiler and follows the live APNAP order. The original trusted resolving effect
and exact root/store remain bound throughout. Movement waits for every required
group; one canonical split transaction commits all arrivals and then publishes
one permanent departure plus physical component arrival facts. Ordinary events
and earlier ordering schemas retain their existing representations.

Unknown scopes, stale stores, unauthorized actors, malformed saved results and
incomplete permutations reject. The represented path retains its guards for
per-part counter modifiers, token-parent commanders, linked returns, costs,
player departure and multi-object resolving continuations. This changes no
casting permission and grants no Mutate or original-card admission by itself.

## Alternatives

Applying a card-only effect to the whole token parent prevents a legal token
death. Ignoring the effect loses the physical card destination and counter.
Independent nested choices force the parent replacement before its card-only
competitor. A second replacement registry or split mutation owner duplicates
existing rules authority and rollback responsibility.

## Consequences

Model controls distinguish both replacement orders, scoped counter retargeting,
closed save/load data and separately owned destination permutations. Actual
Cackling Counterpart and Murder witnesses distinguish top-card and top-token
behavior, pending exiler ordering, rejected-command rollback, partial-arrival
fault rollback, token cleanup and full replay. The ambient card-only effect is
an existing reviewed replacement control, without a whole-card support claim.
