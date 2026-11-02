---
title: "ADR 0160: versioned completed characteristic queries"
status: "ADR"
authoritative_source: "shared continuous source selector and record state provenance"
verified: "2026-11-02"
audience: "rules, replay, compiler and architecture contributors"
maintenance: "hand-maintained"
adr_id: "0160"
decision_status: "accepted"
date: "2026-11-02"
---

# ADR 0160: versioned completed characteristic queries

## Context

The characteristic evaluator includes its requested final layer. Its source
selector previously omitted a static program beginning in that same layer.
Consequently a public query ending at layer five could miss a completed color
setting even though an unrestricted read reported the correct color.

Unchanged main `6cd7935` normally admitted original Steel of the Godhead and
Darkest Hour. After their actual casts, a black Isamaru incorrectly retained
the white-dependent +1/+1 and lifelink. Ten-command replay reproduced that
incorrect result. The cutoff originates in `97f90415` (#519), before the later
privacy and source-removal repairs.

CR 613.1d/e/f applies type and color changes before conditional ability grants.
CR 613.6 and 613.8 still govern when a source effect starts, continues, or loses
to a same-layer dependency. Query completion must preserve those contracts.

## Decision

Current games pin `characteristic_query_boundary_version: 1` in authoritative
checkpoints and record format provenance. Include a source beginning at the
requested final layer through the existing continuous-program selector. Keep
the earlier-source resolver and layer-six participation/dependency graph.

Historical absence retains the former cutoff, remains absent on serialization,
and preserves earlier command behavior. Reject booleans, unknown state versions,
and disagreement between format provenance and the checkpoint. Existing declared
runtime provenance retains its independent rejection contract.

This correction adds no second evaluator, component registry, ordering scheme,
card-specific dispatch or persistent characteristic cache. It does not skip the
layer-six snapshot or disable source authority. It expands no card grammar.

## Consequences

Actual Steel/Darkest casts remove the white-only bonus and lifelink. Returning
the color setter restores the condition. Original Ensoul Artifact animation
appears at the completed type boundary and survives later Humility ability
loss. Focused witnesses retain wrong-principal rollback, private projection,
save/load, replay, historical absence, and the existing Ashaya/Toxicrene
source-start control. A live boundary omission fails the actual stat consequence.

Support classification and native behavior remain separate. Canonical generation
measures corpus effects for this correctness/non-harvest transition; none are
inferred from the repaired cards' existing support status.

## Alternatives

Skipping the layer-six snapshot changes existing bounded-view behavior before
source eligibility has been established. Disabling the earlier-source resolver
loses the CR 613.6 distinction. A separate color evaluator or named-card exception
would duplicate authority. Changing historical absence to current behavior would
invalidate retained command hashes. Keep the shared selector and an explicit
versioned completion boundary instead.

## Removal condition

Retire the marker only through an explicit historical compatibility transition
that preserves the completed-view and source-start distinctions for all retained
records. Keep one canonical source selector and layer evaluator.
