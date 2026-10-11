---
title: "ADR 0114: player speed and Max Speed applicability"
status: "ADR"
authoritative_source: "quorune/rules/player_speed_state.py, player_speed_model.py, and compiler/max_speed_nodes.py"
verified: "2026-10-10"
audience: "rules, compiler, replay and architecture contributors"
maintenance: "hand-maintained"
adr_id: "0114"
decision_status: "accepted"
date: "2026-10-10"
---

# ADR 0114: player speed and Max Speed applicability

## Context

Start Your Engines starts a persistent player speed designation through a
state-based action. Once the player has speed, the rules supply a source-free
trigger on the first qualifying opponent life-loss event of each own turn.
The designation and inherent ability persist after the starter leaves. Max
Speed grants abilities only while the current controller's speed equals four,
including abilities that function from graveyards.

## Decision

Use one typed player speed model and canonical mutation owner. An absent speed
remains distinct from a speed of zero. State-based stabilization starts speed
only for a player who controls a current trusted starter and has no speed.
The existing life-total owner reports committed loss groups to this owner,
which records once-per-turn usage when the inherent trigger is created and
queues a source-free stack item through ordinary APNAP placement. Resolution
uses the closed `increase_speed` operation in the existing effect registry.
The instruction retains its controller-bound result and intervening upper
bound when copied or controlled independently of a permanent source.

Reuse the existing static parent/child component scope for Max Speed
activations, triggers and exact static replacement children. Its new equality
predicate reads a derived controller speed alongside existing source-local
Class designations. Fixed characteristic bodies use the existing public-state
condition owner. Unknown Max Speed bodies remain residual; the prefix cannot
be discarded as a decorative ability word and produce an unconditional rule.

The exact architecture baseline admits the reviewed operation and canonical
player speed mutation owner. Engine methods, runtime Oracle interpretation,
card-identity dispatch, unowned writes and oversized-symbol ceilings receive
no growth allowance. Existing effect, component and capability registries
retain their ownership.

## Alternatives

A permanent counter would move or disappear with its source. Recomputing speed
from turn history would not preserve queued or countered inherent triggers.
Treating Max Speed as a resolution-time check would erase an ability that was
already triggered. Independent speed, event or component registries would
duplicate existing authority.

## Consequences

Player speed and inherent usage use ordinary state persistence and exact replay.
Source control changes can start the new controller's speed without moving the
prior controller's designation. Public speed changes invalidate yielding so
newly meaningful Max Speed actions remain available. Historical records without
speed fields retain their existing payloads and require the repository's
ordinary matching execution provenance.

## Verification

Focused witnesses cover state-based start, own-turn opponent loss, repeated
losses, source departure, controller changes, prevented loss, inherent trigger
countering, Max Speed availability in battlefield and graveyard contexts,
pending save/load, exact replay, malformed descriptors and omission mutants.
Complete public certification follows the coherent combined delivery batch.
