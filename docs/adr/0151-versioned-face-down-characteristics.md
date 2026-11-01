---
title: "ADR 0151: versioned face-down characteristics"
status: "ADR"
authoritative_source: "Copy-value, object-identity and trusted stack-resolution owners"
verified: "2026-10-31"
audience: "rules, persistence and replay contributors"
maintenance: "hand-maintained"
adr_id: "0151"
decision_status: "accepted"
date: "2026-10-31"
---

# ADR 0151: versioned face-down characteristics

## Context

CR 707.2 and 708.2 make a face-down object's listed characteristics its copiable
values. The prior copy reader used compiled printed values. Copying a Morph
spell could therefore expose its hidden physical name, Oracle identity, rules
text and cost to opponents. A printed land's Morph cast also reached empty
resolution through the printed land type instead of its current creature type.

Correcting these operations changes serialized copy objects and command hashes.
Historical Game Record absence and strict declared runtime provenance remain
independent compatibility requirements.

## Decision

New games carry `face_down_characteristics_version: 1` in their checkpoints and
matching record format provenance. Absence selects the previous execution and
stays absent on serialization. Only absence or integer one is valid. Manifest
and initial-checkpoint versions must agree.

The existing characteristic reader supplies the represented layer-1b values.
The copy adapter retains only those values and an anonymous copiable identity.
An explicitly empty name stays empty. Copy-object and token-snapshot identity
planning cannot substitute the hidden physical Oracle or image identity.
Listed abilities such as Disguise Ward remain copiable; printed catalogs and
mana-production metadata do not. A copy is face up and has no turn-up action
from the hidden original. Ordinary target, source, replacement, mutation and
projection ownership remains unchanged.

Anonymous noncard spell copies have no physical card record. Their builtin
execution provenance remains the existing provenance; record-trust validation
does not change. Current empty face-down spell resolution validates the complete
current method descriptor, exact builtin identity, fingerprint, status and
destination before applying the creature characteristics on the stack. A
printed land type cannot override that validated cast contract.

The marker is independent of timestamp semantics and unrelated copy extensions.
It neither changes timestamp versions nor authorizes another behavior to reuse
its historical execution mode. Declared runtime-trust mismatches still reject
before commands; historical records are not rewritten or stripped of provenance.

## Alternatives

Projection-only concealment leaves the copied rules behavior incorrect. An
unconditional correction changes historical command hashes. Deriving execution
authority from a hidden Oracle identity or semantic-key text weakens provenance.
A second copy or token mutation owner duplicates existing authority. Coupling
the correction to a broader unrelated copy mode makes future compatibility
depend on work outside this repair.

## Consequences

Current copy values and public identity follow the face-down rules. The original
physical card retains its separate permitted turn-up action. Source data,
malformed frames, principals, rollback, pending state and replay remain bounded
by their existing owners. Historical absence preserves its former representation
and resolution boundary. This decision adds no Oracle grammar or support harvest.
