---
title: "ADR 0174: Attached permanent destruction"
status: "ADR"
authoritative_source: "CR 301.5, 303.4, 608.2h and 701.8"
verified: "2026-11-16"
audience: "compiler, query, destruction and replay contributors"
maintenance: "hand-maintained"
adr_id: "0174"
decision_status: "accepted"
date: "2026-11-16"
---

# ADR 0174: Attached permanent destruction

## Context

Some destruction instructions affect attachments to a current creature, land or
player. The anchor can be targeted while the attachments are untargeted. A union
of creatures and their attached permanents is one simultaneous affected set.
Separate destruction instructions would change replacement and death behavior.

## Decision

Represent four closed scopes with an explicitly tagged version-one
`AttachmentDestructionSetSpec` within the existing affected-set type. Preserve
the historical base type, serialized field sets and destruction intent layouts.
The existing codec recognizes the new tag through its strict owned decoder.

Read current effective public object rows and the reciprocal attachment owner
only for the new set. Select and deduplicate the full set before canonical
destruction preflight. Player attachments use the same reciprocal authority as
object attachments. Do not reinterpret the historical object-row projection.

Use the existing `destroy_all` operation, semantic handler, continuation identity,
destruction plan and zone owner. Require the fine capability independently from
the new serialized payload. Targeted anchors use existing offer, command and
resolution legality. Descendant attachments are affected without being targets.

## Alternatives

- Sequential creature and attachment destruction would lose simultaneity.
- A new operation or mutation owner would duplicate the existing transaction.
- An unmarked change to base set fields would reinterpret historical payloads.
- Following attachment links after moving the anchor would lose the original set.

## Consequences

Current types, phasing, logical identity and attachment changes determine the
resolution snapshot. Existing indestructible, shield, regeneration, replacement,
event ordering and rollback owners apply to that complete snapshot. Chosen
types, recursive attachment closure and unspecified results remain unrepresented.
