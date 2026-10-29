---
title: "ADR 0138: Forecast hand-source disclosure"
status: "ADR"
authoritative_source: "Forecast model and existing activation, reveal, zone and turn owners"
verified: "2026-10-28"
audience: "rules, compiler and persistence contributors"
maintenance: "hand-maintained"
adr_id: "0138"
decision_status: "accepted"
date: "2026-10-28"
---

# ADR 0138: Forecast hand-source disclosure

## Context

Forecast activates from a card's owner's hand during that owner's upkeep, once
each turn. Its source remains revealed until it leaves the hand or a step that
is not an upkeep begins. The existing activated catalog already owns hand-zone
abilities, upkeep conditions and per-incarnation usage. The existing scoped
hand-reveal code owns knowledge and current disclosure. A new implementation
must preserve these responsibilities and historical descriptor shapes.

## Decision

An optional true `forecast` marker in the existing activated descriptor opts
into the complete hand/upkeep/once and reveal rule. The model validates those
intrinsic restrictions. The compiler removes only the recognized source reveal
from mechanical cost parsing and retains the full cost for presentation.
Unknown costs, missing reveals and unknown bodies remain residual.

The activation commit owner delegates the source reveal to the canonical
scoped hand-reveal mutation owner. `ForecastRevealFrame` retains the source's
original logical identity, owner, turn and prior disclosure. The turn owner
ends that scope on a non-upkeep step, and the zone owner ends it only when an
actual hand departure commits. Consecutive upkeeps do not end it. Learned
identity persists, and later hand incarnations have independent usage.

Schema two of the existing selected-tap cost adds fixed ordinary color
alternatives. Its plan retains the ability source zone separately from the
battlefield payment objects. Forecast uses owner-hand validation; prior callers
retain battlefield source checks. The existing selected-tap owner validates
current predicates and distinct incarnations and commits the group atomically.

## Alternatives

A separate hand-ability executor would duplicate legality, targeting, usage and
payment authority. Treating the Forecast label as decorative would omit its
intrinsic restrictions. Revealing only when the ability resolves would disclose
too late. Ending the reveal at ability resolution would shorten its required
lifetime. Mutating reveal state while merely preparing a zone change could end
it without an actual departure. Requiring a battlefield ability source for a
selected-tap cost would reject legal hand activations after offering them.

## Consequences

Earlier activated descriptors omit the marker and keep their existing codecs.
Earlier tap costs retain schema one and battlefield-source defaults. The new
frame lives on its source incarnation; no game-state mode or checkpoint field
is introduced. Existing opening-hand reveal code delegates unchanged begin/end
mutations to the shared scoped owner. Card recognition, strict closure and
successful native composition remain distinct; unknown companion bodies are
explicit unfinished support.
