---
title: "ADR 0111: source counter scalar amounts"
status: "ADR"
authoritative_source: "quorune/scalar_effect_amount_model.py, scalar_effect_amounts.py, and compiler/scalar_effect_amounts.py"
verified: "2026-10-10"
audience: "compiler, rules, replay, and architecture contributors"
maintenance: "hand-maintained"
adr_id: "0111"
decision_status: "accepted"
date: "2026-10-10"
---

# ADR 0111: source counter scalar amounts

## Context

Result amounts based on counters on the source require current values while
that object remains available and last known values after it departs.
Sacrificing the source to activate an ability must not erase the quantity.
A returning incarnation cannot supply values for the old ability.

## Decision

Extend the existing scalar descriptor with version two for one canonical
source counter name. Version one retains its exact fields and serialization.
The compiler consumes complete source-self counter phrases and proves their
result shape through the existing fixed-result projection. Ambiguous target
and event-object counter references remain residuals.

The shared scalar source snapshot optionally includes counter state only when
the program requires it. Existing activation preparation, trigger discovery,
and predeparture pinning retain the original source incarnation. Resolution
reads current counters for that same object and otherwise uses sealed last
known counters. Missing snapshot fields are unavailable, not a zero count.
The existing amount binding cache still freezes a declaration at its printed
instruction point and validates subsequent continuations.

## Alternatives

Reading the physical card after a zone change would substitute a new object.
A public count proxy has no source identity and cannot own counter look-back.
A separate draw or damage path would duplicate canonical result ownership.

## Consequences

Source counter draw, life, token, and independently exact result leaves
compose through the shared scalar boundary. Unsupported siblings, nonfixed
coefficients, ambiguous references, and unrepresented ordered bodies remain
residuals. Existing version-one payloads retain their compatibility contract.

## Verification

Real source death, sacrifice activation costs, response counter placement,
entry replacement quantities, pending target selection, and blink distinguish
current values from last known values. Codec, omission mutation, privacy,
save/load, and pre-action replay use the existing trust and certification gates.
