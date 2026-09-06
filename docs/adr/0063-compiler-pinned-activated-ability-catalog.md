---
title: "ADR 0063: compiler-pinned activated-ability catalog"
status: "ADR"
authoritative_source: "CardProgram activated-ability compilation and runtime discovery boundary"
verified: "2026-09-05"
audience: "compiler, rules, replay, and architecture contributors"
maintenance: "hand-maintained"
adr_id: "0063"
decision_status: "accepted"
date: "2026-08-11"
---

# ADR 0063: compiler-pinned activated-ability catalog

## Context

Activation offers and commits already used typed ability values, but several
runtime paths still rebuilt those values from current Oracle prose. Ordinary
Crew, Cycling, fixed-output mana, and color-set mana also had separate runtime
discovery adapters. That made the compiler and the running game competing
authorities, made copied and granted abilities hard to represent uniformly,
and allowed a later parser change to alter a game without changing its pinned
CardProgram.

## Decision

The Oracle compiler attaches one versioned
`activation.catalog.pinned.v1` descriptor to every represented activation
program. The descriptor contains the complete closed `ActivatedAbility` value,
including costs, zones, timing, targets, usage limits, output modes, typed
conditions, dynamic mana-output kind, and mana-spend restriction. Runtime
discovery reads only current source-pinned CardPrograms, intrinsic abilities
granted by basic land types, typed continuous-effect fragments, or typed token
characteristics.

Crew, Cycling, and the fixed and color-set mana compilers remain the owners of
their specialized semantic behavior. Their descriptors lower into the same
catalog during compilation; the former specialized runtime discovery modules
are removed. The catalog is metadata for discovery and replay identity, not an
independent resolution capability, so it does not promote a program or replace
the specialized capability closure.

Layer 1 copy values carry the serialized catalog, layer 6 typed grants add
closed ability fragments, and effects that remove all abilities remove the
catalog. A text-changing effect clears descriptors compiled from the replaced
text until a typed text-change transformation is supported. Standard Treasure,
Food, and Map tokens receive closed descriptors at token construction rather
than executable rules-text markers.

The universal semantic operation `grant_ability_fragment` is the reviewed
entry point for a compiled effect to grant an executable activated ability. It
accepts only the versioned, closed ability-fragment vocabulary and lowers that
fragment before it reaches layer 6. It cannot carry an arbitrary callback,
Oracle text, card identity, or mutable state reference. The registered
operation is therefore part of the exact architecture allowance bound to this
ADR; widening its payload requires a new review and baseline.

Activation restrictions are parsed once into the same pinned ability value.
The closed public family includes controller upkeep, the controller's turn
before attackers are declared, fixed controller battlefield or graveyard
queries, and identity-free controller hand counts. Battlefield queries use
current effective type, subtype, supertype, color, and represented keyword
facts, including the shared layer-6 applicability path; hidden-zone queries
count objects without inspecting identity. The canonical activation
availability owner evaluates the descriptor both for an advertised action and
again at commit before mana, tap, usage, source-zone, or stack mutation.

Opponent and target-player zones, hidden identity, attachments, counters,
combat or tapped state, chosen or named cards, same-name and distinct-value
queries, total power, source-relative or historical facts, disjunctions,
compound conditions, arbitrary timing windows, and once-ever limits remain
residual. This family does not create an effect-specific activation check or a
second query/commit path.

Historical Game Record v3 data may use its isolated compatibility adapter to
reconstruct legacy descriptors. Current games cannot use that adapter. The
runtime never calls the Oracle activation parser, and malformed or conflicting
catalog descriptors fail closed before action exposure.

## Alternatives

- Keep parsing effective Oracle text during every offer. Rejected because it
  bypasses the pinned CardProgram and changes replay semantics when the parser
  changes.
- Keep one discovery module per mechanic. Rejected because copying, removal,
  grants, conditions, and action identity need one typed catalog even though
  specialized mechanics retain their own execution owners.
- Treat added rules text as executable. Rejected because display provenance is
  not a typed ability and cannot safely participate in costs, activation
  discovery, capability closure, or replay fingerprints.

## Consequences

- Activation advertisement and acceptance consume the same immutable ability
  value.
- A condition that changes after proposal construction rejects the commit
  before any activation cost mutates state.
- Copy, typed grant, token construction, removal, replay, and source validation
  share one versioned serialization.
- Parser changes affect newly compiled CardPrograms, not an in-progress game.
- Unsupported activation conditions and mana behavior remain explicit
  residuals rather than being guessed from prose.
- The legacy Game Record v3 adapter remains necessary until historical records
  no longer require it; it is not available to current-game execution.
