---
title: "ADR 0150: versioned face-status timestamps"
status: "ADR"
authoritative_source: "GameState codec, record provenance and zone object-state ownership"
verified: "2026-10-31"
audience: "rules and replay contributors"
maintenance: "hand-maintained"
adr_id: "0150"
decision_status: "accepted"
date: "2026-10-31"
---

# ADR 0150: versioned face-status timestamps

## Context

CR 613.7f gives a permanent a new timestamp when it turns face up or face down.
The represented Morph and Manifest special actions previously changed face
status while retaining the earlier timestamp. Applying the correction to saved
commands would change authoritative replay hashes. Transform timestamp semantics
have their own independent marker under [ADR 0149](0149-versioned-transform-timestamps.md).

## Decision

New games carry `face_status_timestamp_version: 1` in their checkpoints and
matching manifest format provenance. `ZoneTransitionOwner.turn_face_up` validates
the current object and planned timestamp, delegates face-state mutation to
`zone_object_state.turn_card_face_up`, and advances the existing canonical clock
before any face-up counter or trigger observers run.

An ineligible or suspended command retains the original clock and face state
through the existing transaction and continuation owners. A cost that moves the
source out of its original battlefield incarnation ends without a face-up event
or face-status timestamp. Logical identity, control acquisition, counters,
attachments and damage remain separate from timestamp assignment.

Historical absence selects the former behavior and remains absent on
serialization. Only absence or integer one is accepted. Manifest and initial
checkpoint markers must agree. The face-status marker is independent of the
transform marker, so records that pin transform semantics can still retain their
earlier ordinary turn-up behavior.

## Alternatives

Rewriting saved records loses the original evidence. Reusing the transform
marker would change ordinary face-up behavior for records that already carry
that marker. A second clock or face-state mutation owner would duplicate the
existing authoritative boundaries.

## Consequences

Current face-up special actions receive the timestamp required by the pinned
rules. Matching-provenance legacy records retain their original hashes. Declared
runtime-trust identity remains exact. This decision adds no grammar, card
admission, or general face-down effect family; those operations retain their
own represented contracts.
