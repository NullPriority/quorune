---
title: "ADR 0139: static source removal dependencies"
status: "ADR"
authoritative_source: "continuous effect ordering and characteristic evaluation host"
verified: "2026-10-28"
audience: "rules, replay and architecture contributors"
maintenance: "hand-maintained"
adr_id: "0139"
decision_status: "accepted"
date: "2026-10-28"
---

# ADR 0139: static source removal dependencies

## Context

[ADR 0125](0125-layer-six-source-participation.md) evaluates source participation
through the prefix before a printed effect begins. That prefix needs the
CR 613.8a dependency between an ability remover and an effect whose existence
it changes. Timestamp order alone can retain a removed static ability.

CR 613.6 preserves earlier-layer portions. The pinned Toxicrene ruling retains
Ashaya's type-changing effect; it does not exempt a separate layer-six source
from same-layer dependencies. Dress Down prevents Toxicrene's land ability
from starting regardless of their timestamps. Toxicrene similarly removes a
land creature's separate keyword-giving ability before that ability applies.

## Decision

Keep the existing collector, typed characteristic evaluator and prefix query.
Build one shared source-removal dependency graph for layer-six producers.
Evaluate removal applicability after completed earlier layers. Use the canonical
ordering owner, including its dependency-loop timestamp fallback, for every
source prefix. Exclude an inapplicable earlier producer through the existing
participation query. Keep the graph within the synchronous pure read frame.

## Alternatives

An end-of-layer snapshot loses already-started effects. Separate per-source
orders can disagree and recurse. Timestamp-only ordering misses dependencies.
Card-specific exceptions cannot establish generic participation.

## Consequences

Ability loss ends the represented conditional recipient grants and restores
opposing target protection before resolution. Earlier-layer type changes keep
their CR 613.6 behavior. Native witnesses cover removal before and after the
source, current types, control, departure, private projection and exact replay.
No serialized state, registry or long-lived cache is added.

## Removal condition

Replace this graph only when a shared effect lifecycle owner preserves the
same dependency, loop, earlier-layer and replay contracts.
