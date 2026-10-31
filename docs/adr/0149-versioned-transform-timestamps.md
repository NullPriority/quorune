---
title: "ADR 0149: versioned transform timestamps"
status: "ADR"
authoritative_source: "GameState codec, record provenance and permanent transform ownership"
verified: "2026-10-31"
audience: "rules and replay contributors"
maintenance: "hand-maintained"
adr_id: "0149"
decision_status: "accepted"
date: "2026-10-31"
---

# ADR 0149: versioned transform timestamps

## Context

CR 613.7g gives a double-faced permanent a new timestamp when it transforms or
converts. The earlier transform owner retained its timestamp while correctly
retaining the logical object. Applying a global correction to old recorded
commands would change authoritative hashes during replay.

## Decision

New games carry `transform_timestamp_version: 1` in the checkpoint and matching
manifest format provenance. Successful represented double-faced transforms draw
their timestamp from the existing zone owner's monotonic clock. The transform
owner retains face and transform-count mutation. Stale counts and ineligible
instructions consume no timestamp.

Simultaneous groups follow live APNAP order. Controllers order multiple own
permanents through the existing capability and ordering-choice form. A typed
continuation holds the original batch and step until all permutations are known;
faces and timestamps commit together through the transform owner. The original
untap or state-action path resumes under scoped immutable selection data. A
departing participant causes the remaining batch to be rebuilt before commitment.

Historical absence selects the former behavior and serializes without a marker.
The decoder accepts only absence or integer one; record provenance must match the
initial checkpoint. Logical incarnation, controller, counters, damage,
attachments and control acquisition remain separate from timestamp assignment.

## Alternatives

Rewriting older commands or hashes loses the original evidence. Changing all
replays to the new behavior violates the historical contract. Keeping unchanged
timestamps for new games contradicts the pinned rule. A versioned checkpoint
distinguishes the two contracts without a second transform or timestamp owner.

## Consequences

Current characteristic ordering sees the new transform timestamp. Exact
historical replay retains its former state and source provenance. This decision
adds no transform grammar or card support. Merged permanents are not themselves
double-faced under CR 730.2i and remain a separate composition contract.
