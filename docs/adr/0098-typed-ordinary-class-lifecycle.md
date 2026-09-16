---
title: "ADR 0098: typed ordinary Class lifecycle"
status: "ADR"
authoritative_source: "Class compiler context, permanent designation owner, shared static-component applicability, and cleanup hand-size query"
verified: "2026-09-16"
audience: "rules, compiler, replay, cleanup, and architecture maintainers"
maintenance: "hand-maintained"
adr_id: "0098"
decision_status: "accepted"
date: "2026-09-16"
---

# ADR 0098: typed ordinary Class lifecycle

## Context

An ordinary Class level bar represents both an activated ability and a static
ability that exposes the abilities printed in that section. The permanent's
level is a public noncopiable designation rather than a counter. It survives
type loss, ability loss, control changes, and phasing on the same object, but a
zone change creates a new object. Flattening the printed Class text into
always-active abilities would violate both the activation restriction and the
layer-6 current-ability boundary.

The first bounded compiler pass made the reminder, activations, and paired
static scopes exact, but generated measurement correctly rejected a transition
with no positive complete-card lower bound. Completing one real carrier also
required the reusable source-self level-change occurrence and the current
no-maximum-hand-size cleanup permission. The designation transition needs one
closed resolution operation; omitting that operation from the explicit
semantic validity inventory causes the universal-operation architecture guard
to fail even when the handler itself is registered.

## Decision

Register `gain_class_level` as one reviewed universal semantic operation. Its
closed shape accepts only the resolving source zone object and level 2 or 3.
The effect-runtime family validates the trusted stack source context and
delegates mutation to `permanent_designations.advance_class_level`; it does not
write `GameState` directly, accept callbacks, inspect Oracle prose, or select
behavior by card identity.

The ordinary Class compiler recognizes exactly one fixed ordinary-mana Level 2
bar followed by one Level 3 bar. Each bar emits a normal sorcery-speed
activation and a separate static scope. Sequential legality uses the canonical
activation condition owner, and each child uses the shared static-component
applicability map. Level advancement dispatches one normalized source-self
event only after the designation changes, so newly applicable child triggers
use ordinary current-ability and APNAP placement.

The exact `You have no maximum hand size` static permission is a separate
reusable component. Cleanup advertisement and commit validation query the same
current component snapshot. Removing the component restores the ordinary
discard requirement; no persistent player statistic is rewritten.

Game Record v3 remains additive: the explicit Class level is omitted at its
historical level-1 default, normalized trigger placement uses existing stack
records, and the cleanup permission derives from compiler-pinned programs.

## Alternatives

- Store Class level as a level counter. Rejected because CR 716 explicitly
  separates the designation from Leveler counters and copying behavior.
- Gate child abilities in activation and trigger call sites independently.
  Rejected because addition and removal must share one layer-6 applicability
  query.
- Treat the level change as an arbitrary annotation or generic callback.
  Rejected because identity, replay, and mutation ownership would be implicit.
- Suppress cleanup discard by mutating `PlayerState.max_hand_size`. Rejected
  because current ability removal and copying require a live component query.
- Weaken the transition or universal-operation guards. Rejected because the
  generated zero-card result and semantic inventory omission were both valid
  fail-closed findings.

## Consequences

Ordinary Class activation offers and commands share one legality path, resolve
through the normal stack, and cannot affect an absent or re-entered source.
Higher levels add child components without replacing lower ones. Copies use
their own level designation. Public projection, save/load, exact replay, and
cleanup privacy remain under existing owners.

The reviewed universal operation increases the explicit semantic vocabulary by
one while adding no engine method, engine-local write, unowned write, runtime
Oracle-text access, or card-identity dispatch. The architecture baseline is
bound to this exact allowance and the semantic-handler inventory test remains
the required feature-PR sentinel for future operation additions.

Malformed or nonordinary level bars, direct level setting or removal, broader
maximum-hand-size modifiers, independently unsupported Class children, and
cycle-sensitive dynamic characteristic/type-change interactions remain
fail-closed.

## Removal condition

Retire `gain_class_level` only if a successor preserves closed source-spanned
validation, sequential activation legality, source-incarnation pinning,
noncopiable designation semantics, shared layer-6 child applicability,
normalized trigger placement, cleanup current-component behavior, rollback,
privacy, save/load, and exact replay without runtime Oracle parsing, card
identity, callbacks, or direct state mutation.
