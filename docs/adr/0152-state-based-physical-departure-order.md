---
title: "ADR 0152: state-based physical departure order"
status: "ADR"
authoritative_source: "State-based execution, physical composition and authenticated decision owners"
verified: "2026-10-31"
audience: "rules, persistence and replay contributors"
maintenance: "hand-maintained"
adr_id: "0152"
decision_status: "accepted"
date: "2026-10-31"
---

# ADR 0152: state-based physical departure order

## Context

CR 704 performs applicable state-based actions simultaneously. CR 730 preserves
one permanent departure and every physical arrival. Under CR 404.3, the owner
orders all cards entering the same graveyard together, including ordinary
companions and components of different composed permanents. The resolving-effect
continuation in ADR 0148 requires a trusted parent instruction and cannot
represent a state-based death without inventing stack authority.

## Decision

Retain the complete prepared state-based destruction and destination batch.
`CompositionDepartureBatchRequest` identifies its current subjects, physical
stores, destination ordering groups and surviving APNAP order. The closed
state-based continuation also retains the public battlefield/turn frame,
replacement selections and collected permutations. Its decision kind is
`permanent.composition_order`; it uses the existing generic ordered-reference
form and authenticated pilot capabilities.

Collect every required owner order before movement. A retained turn anchor
remains separate from the surviving choice participants. Completion compares the
current frame and recomputed batch, rejects changed principals and payloads,
then resumes the ordinary state-based owner. The typed prepared batch enters
the existing zone port once, after all replacement and physical choices finish.

The physical mutation owner handles both the existing single-effect adapter
and the complete batch. It captures source information before movement, commits
all physical and ordinary arrivals through canonical reset/placement ports,
records all zone histories, then discovers one combined trigger batch using
the pre-departure sources. This preserves both a dying observer and the
distinction between one permanent death and several physical-card arrivals.
Failure during commitment rolls the whole transaction back.

Ordinary uncomposed movement and existing semantic ordering frames keep their
interfaces. No new mutation registry or record-trust exception is introduced.
Physical commander, mixed token-parent/card-only replacement, linked-return,
cost and player-departure continuations retain explicit unfinished boundaries.
This decision grants no Mutate casting permission or original-card admission.

## Alternatives

Reusing a semantic frame fabricates a parent instruction for an automatic
rule. Committing one composition at a time publishes a partial simultaneous
event. Separate permutations for each composition prevent legal interleaving
with other cards entering the same graveyard. An independent zone mutation
owner would duplicate reset, replacement and event authority.

## Consequences

State-based ordering is authenticated, serializable and separate from stack
resolution. Preparation retains the entire batch while choices remain pending.
Native damage, zero-toughness, simultaneous source-departure, rejected-command,
pending-load and replay witnesses cover the distinctions at this boundary.
