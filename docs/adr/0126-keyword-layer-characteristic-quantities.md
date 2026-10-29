---
title: "ADR 0126: keyword-layer characteristic quantities"
status: "ADR"
authoritative_source: "characteristic quantity codec, characteristic evaluator and dynamic quantity owner"
verified: "2026-10-27"
audience: "rules, compiler, replay and architecture contributors"
maintenance: "hand-maintained"
adr_id: "0126"
decision_status: "accepted"
date: "2026-10-27"
---

# ADR 0126: keyword-layer characteristic quantities

## Context

A modifier that counts creatures with Flying must see granted and removed
keywords. The existing quantity boundary stops after color evaluation so that
ordinary later-layer statistics cannot recursively determine their own inputs.
Widening every quantity to a complete characteristic view would lose that
boundary and could create cycles.

## Decision

Characteristic quantity schema six represents one closed public battlefield
object count through layer six. It retains the existing relation and source
exclusion fields, uses the shared object predicate, and requires the separately
inferred `quantity_expression.keyword_layer_count` capability. The existing
versions retain their payloads and evaluation defaults.

The new count supplies later-layer modifiers. It does not supply a
characteristic-defining power/toughness value or an ability grant controlled by
the same quantity. A view ending before layer seven skips its count before
materializing the modifier. Candidate objects are evaluated through layer six,
with phased-out objects excluded. Hidden-zone, numeric, nested and unknown
predicates remain outside this version.

## Alternatives

Keeping layer-five reads would miss granted or removed keywords. Reading full
characteristics for every quantity would allow a later statistic to determine
its own inputs and would change older descriptor behavior. Parsing keyword
text during evaluation would bypass the existing typed object-query authority.
The explicit version retains the old boundary while representing this later
public dependency separately.

## Consequences

The compiler can reuse reviewed public qualifier grammar while preserving the
outer quantity's controller relation. Native witnesses distinguish current
granted keywords from printed text, foreign flyers from controlled flyers,
and the modifying source from other counted objects. Pending load and replay
retain the explicit descriptor version without adding a GameState field.

This version creates no mutable characteristic cache or new mutation owner.
Broader dependency cycles or same-layer ability-dependent quantities require
their own representation and assurance rather than a wider view flag.
