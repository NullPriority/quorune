---
title: "ADR 0147: versioned token copy values"
status: "ADR"
authoritative_source: "Copy-value planning, GameState semantics and Game Record provenance"
verified: "2026-10-30"
audience: "rules, persistence and replay contributors"
maintenance: "hand-maintained"
adr_id: "0147"
decision_status: "accepted"
date: "2026-10-30"
---

# ADR 0147: versioned token copy values

## Context

CR 707.2 copies characteristics rather than source cleanup or designations.
The former direct token-copy path copied source annotations, while the canonical
snapshot path already kept copiable values separate. Changing direct copies to
use that snapshot changes both gameplay state and its serialized representation.
Populate reaches the direct path through an unchanged choice instruction.
Copy effects must also supersede a token's original characteristic definition.

Game Record v3 supports absence of additive runtime-trust provenance. A
historical absence-shaped Populate record can therefore reach the changed
operation, even though a record pinned to a different declared runtime is
already rejected before commands. Readable checkpoint data alone does not prove
that historical execution keeps its hashes.

## Decision

New GameState instances use `token_copy_value_version: 1`, mirrored in record
format provenance and checked against the initial checkpoint. Version one uses
canonical copiable snapshots for direct copies and names new copies from their
current copiable values. New tokens retain the mode as a noncopiable annotation
so base characteristic and activated-ability reads prefer a current copy effect
over their original token definition. Preview and commit share the same planner.

Historical absence remains absent on serialization. It keeps the former direct
copy annotation payload, snapshot/copy label selection and original-definition
precedence. No command upgrades the mode. Unknown or Boolean versions and
manifest/checkpoint disagreement reject. Defined transforming-token face data
has its own closed schema and physical role; it does not infer physical faces
from an ordinary copy effect.

In current copy-value mode, creation as a copy of a represented physical
double-faced card captures both faces through detached characteristic reads.
The existing face value model keeps its schema-one fixed definitions and adds
schema two for full compiled copiable values. Each side retains its activation
catalog, fragments and source provenance. Physical orientation identifiers are
separate from copied characteristic names, which can be equal on both sides.
Creation applies copy exceptions independently to each side before freezing
physical identity. A merged permanent contributes only its single current
copiable set. Historical absence retains the earlier Oracle-card snapshot.

The same current-mode face transport applies when copying a represented
double-faced permanent spell. Its immutable current-or-departure source keeps
both faces before the original leaves the stack. The canonical copy-object
owner establishes physical identity when creating the stack copy; resolution
preserves that data while the same object becomes a token. This is not token
creation. Historical absence keeps the former spell-copy snapshot and does not
gain physical faces during replay.

Face-down values, anonymous identity and method-based empty resolution use the
independent `face_down_characteristics_version` contract in
[ADR 0151](0151-versioned-face-down-characteristics.md). Those rules remain
operative when `token_copy_value_version` is absent. General copy-value mode
does not select or upgrade a record's face-down behavior.

Keep the existing runtime-trust policy: a record declaring a different runtime
identity remains explicitly incompatible. Do not remove or replace its
provenance to make it execute. Synthetic absence-shaped regression inputs are
separate fixtures; the source archive and all gameplay hashes remain unchanged.

## Alternatives

An unconditional correction breaks historical direct-copy hashes. Versioning
only compiler output misses Populate, builtins and existing copy APIs. A second
mutation owner duplicates token entry and replacement authority. Replacing
recorded hashes or runtime provenance would discard the replay contract.

## Consequences

Current games isolate copiable values through the existing token owner.
Historical mode deliberately preserves prior behavior for exact reproduction.
Pending choices, controller authority, typed definitions, source cleanup,
provenance rejection and both current and historical replay remain tested
boundaries. This follows the additive state-version approach in
[ADR 0134](0134-versioned-explore-action-semantics.md).
