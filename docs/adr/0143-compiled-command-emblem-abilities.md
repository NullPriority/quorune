---
title: "ADR 0143: compiled command emblem abilities"
status: "ADR"
authoritative_source: "compiled emblem descriptors and trusted command-zone ability references"
verified: "2026-10-30"
audience: "compiler, rules, trust and replay contributors"
maintenance: "hand-maintained"
adr_id: "0143"
decision_status: "accepted"
date: "2026-10-30"
---

# ADR 0143: compiled command emblem abilities

## Context

CR 114 requires emblem abilities to function from their command-zone object.
The creating permanent can leave or change controllers without moving those
abilities to the new controller or ending them. Existing typed static and
trigger owners can execute many of the child abilities, but their source
binding must distinguish an emblem from a battlefield permanent.

## Decision

Compile quoted children with a typeless object source context. Preserve their
source spans, explicit capability dependencies and distinct generated semantic
keys. The first supported owners are characteristic static abilities and
independently closed triggered abilities. Other static owners, unsupported
children and battlefield-incarnation references remain parent residuals until
their own command-zone participation is implemented.

Use one closed versioned descriptor containing the creating Oracle source and
distinct child references, their expected kind, event, display text and complete
declared capabilities. SemanticRegistry remains the only program registry.
Child programs are marked as explicit-reference-only and active in the command
zone; ordinary command-zone cards cannot acquire them as printed abilities.

Add the universal `create_compiled_emblem` semantic operation. Its pure handler
lowers a complete descriptor into one typed intent. The existing emblem owner
verifies every referenced child against the current trusted programs before
allocating or writing the object, and revalidates during commit. Legacy
`create_emblem` display-only operations keep their historical interpretation.

Only actual, owned command-zone emblems may supply the referenced programs.
Continuous and trigger discovery reuse their existing owners with that explicit
source. Each emblem instance retains its own identity, timestamp and controller.
Withheld or changed programs fail closed, and stored display text never becomes
executable authority. Runtime dependency inference independently requires the
compiled-emblem owner and every declared child capability.

The typed descriptor is present only on newly compiled emblems. Legacy objects
retain their existing fields and serialization. Creation remains owned by the
rules boundary accepted in [ADR 0142](0142-typed-emblem-creation-owner.md).

## Alternatives

- Compile an Enchantment and substitute its runtime zone. This invents
  permanent and type assumptions for a nonpermanent object.
- Keep executing from the creating permanent. This gives the wrong lifetime,
  controller, identity and multiplicity.
- Store executable ability prose or introduce another program registry. Both
  duplicate existing authority and weaken deterministic trust.

## Consequences

The compiler can compose independently represented children while preserving
whole-parent rejection. Creation verifies the complete graph before mutation;
discovery validates the real command-zone source and trusted references.
Unknown fields, stale or wrong-source children and underdeclared capabilities
cannot produce a partially functioning emblem. The new operation adds no card
identity dispatcher, callback, engine mutation site or source-text parser.

Native evidence must distinguish creator departure, multiple emblem instances,
actual static or triggered consequences, principal-correct choices, private
projection, pending load, rollback and exact replay. The exact operation
inventory receives only this generic typed operation; existing write, identity,
engine and oversized-symbol ceilings are not widened.

## Removal condition

Retire this descriptor only through a migration preserving typed child closure,
actual emblem source/controller/identity, characteristic layers, event ordering,
principal authority, privacy and historical serialization. Broader child owners
require independent command-zone binding evidence.
