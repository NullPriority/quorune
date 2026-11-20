---
title: "ADR 0178: Qualified death history and source casting"
status: "ADR"
authoritative_source: "Pinned CR 201.2a, 400.7, 601.2, 603.6c and full original Oracle/rulings"
verified: "2026-11-17"
audience: "rules, history, casting, replay and multiplayer contributors"
maintenance: "hand-maintained"
adr_id: "0178"
decision_status: "accepted"
date: "2026-11-17"
---

# ADR 0178: Qualified death history and source casting

## Context

A current-turn graveyard casting permission can depend on a creature's rules
names or subtypes immediately before it died. Its current graveyard object,
current name, source presence when the death happened and presentation log do
not establish that fact. Token deaths and later card movement still count.

## Decision

Move the existing current-turn fact DTOs into `turn_history_model.py`, retaining
all historical `model.py` imports and identical existing codecs. The existing
turn-history owner remains the sole journal; no second history registry or new
mutation owner is introduced.

Add an optional version-one death-name marker to that journal and optional
immutable rules-name tuples to permanent-death events. Absent historical names
remain unavailable; an empty tuple means the object had no name. New games
explicitly enable complete name capture. Capture through the current public
characteristic owner before departure, then record the sealed occurrence.
Reset current-turn events while retaining the explicit recording mode.

Qualified name and subtype exclusions use an explicit new query shape and a
closed source-only graveyard permission. Offers and accepted commands use the
existing zone-permission and casting proposal owners. Printed costs, timing and
prohibitions remain authoritative. Any entry rider must be tied to the actual
graveyard-origin cast and use the existing entry/counter owners.

Every old history/query wire shape and rejection boundary remains unchanged.
Record provenance must match the initial checkpoint. No direct-write, operation,
engine size or other architecture allowance is widened by this extraction.

## Alternatives

Scanning graveyards loses tokens and moved cards. Comparing current names
misses copied departure names. Treating absent names as an empty tuple creates
unsupported eligibility. Tracking these sources in a parallel journal duplicates
authority. A new cast command would separate offers from accepted legality.

## Consequences

Codec, old-hash, provenance, turn-reset and actual named/legacy death replay
evidence verify the history owner. Source-zone permission and entry-rider
integration remain incomplete and require their own behavioral evidence. Full original abilities, normal paid casts, timing,
replacement outcomes, current-turn boundaries, controller/owner contrasts,
privacy, exact replay, rollback, mutation and genuine dependency withdrawal
remain required before scoped readiness. Pure predicate tests alone do not
establish card admission or gameplay support.
