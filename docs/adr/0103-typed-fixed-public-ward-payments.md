---
title: "ADR 0103: typed fixed public Ward payments"
status: "ADR"
authoritative_source: "typed Ward fragment, trigger-processing, static-component, and semantic-choice owners"
verified: "2026-09-22"
audience: "rules, compiler, trigger, continuous-effect, privacy, replay, and architecture maintainers"
maintenance: "hand-maintained"
adr_id: "0103"
decision_status: "accepted"
date: "2026-09-22"
---

# ADR 0103: typed fixed public Ward payments

## Context

ADR 0058 established one typed fixed-generic Ward fragment and one ordinary
trigger-processing owner. Remaining Oracle carriers fragment that boundary in
two reusable ways: fixed-generic Ward appears beside other printed keywords or
inside existing query and attachment grants, and isolated Ward costs may ask
the targeting controller to pay fixed life or discard one card.

Treating those forms as separate triggers or runtime text rules would duplicate
targeting, layer-6 applicability, APNAP placement, private choice, cost, and
counter authority.

## Decision

Keep `WardSpec` as the sole Ward fragment. Historical schema version 1 remains
the exact fixed-generic representation. Schema version 2 represents exactly one
fixed positive life payment or discard-one payment. The compiler admits only
those closed forms and composes fixed-generic Ward with supported printed
keywords, fixed public query grants, and attachment grants.

All static grants use the existing layer-6 component and shared ability-
presence applicability query. No Ward-specific source-applicability test is
introduced. Trigger discovery reads only current effective fragments and
creates one ordinary Ward occurrence per instance with the source controller,
targeting controller, target stack reference, and typed payment frozen.

One semantic-choice handler resolves the Ward occurrence. It delegates mana,
life, discard, and decline to the existing typed payment, replacement-aware
discard, and stack-counter intents. Discard candidates are actor-private and
pin logical incarnation identity. Save/load and replay preserve the typed
payment and continuation rather than reconstructing it from a label or Oracle
text.

## Alternatives

- A second nonmana Ward trigger was rejected because event detection and APNAP
  placement do not change with the cost.
- Reusing casting additional-cost selection directly was rejected because Ward
  pays during trigger resolution and has a different authority and timing
  boundary.
- Parsing the Ward label while it resolves was rejected because labels and
  Oracle prose are not runtime authority.

## Consequences

Fixed-generic Ward now composes with existing keyword and static-characteristic
owners. Fixed-life and discard-one Ward share atomic offer/commit validation,
private projection, stale-incarnation rollback, checkpointing, and exact replay.
Historical schema-v1 records remain readable without reinterpretation.

The generated selector treats a current implementation-backed cohort as the
lowerability proof for its measured exact abilities. Raw unimplemented
candidates still require their frontier lowerability census, and every existing
card, ability, residual, prerequisite, and consecutive-exception threshold is
unchanged.

Random, qualified, multiple-card, optional, alternative, sacrifice, counter,
energy, poison, evidence, blight, waterbend, dynamic, composite, variable,
hybrid, Phyrexian, snow, and restricted costs remain residual. Granted nonmana
Ward outside the closed typed static-component grammar also remains residual.
Dynamic characteristic payment amounts remain outside the cycle-safe trusted
boundary.

## Removal condition

Replace this design only if a successor preserves one current-ability query,
one Ward occurrence owner, schema-v1 history, typed atomic payment, private
discard selection, logical-incarnation rollback, APNAP placement, and exact
replay without runtime prose or card identity dispatch.
