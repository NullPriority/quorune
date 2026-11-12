---
title: "Replay architecture"
status: "current"
authoritative_source: "Game Record v3 implementation, CardProgram V2 registry, and replay verifier"
verified: "2026-08-05"
audience: "engine, persistence, and test contributors"
maintenance: "hand-maintained"
---

# Replay architecture

Game Record v3 is the durable command/evidence boundary. A manifest pins the
engine, profile, decks, card database, semantic registry, seed, trace policy,
CardProgram V2 fingerprints/trust closures, capability registry/evidence,
semantic-handler registry, runtime-component inventory, and lifecycle. Each
accepted command additionally pins the card programs and compact runtime
bindings actually used. Checkpoints accelerate recovery;
commands remain the authority for deterministic verification.

A command-local capture observes the resolver's actual stack objects and
physical spell sources. It includes spells created and completed within one
command while preserving the separate initial-stack snapshot used by ordered
plans. The capture belongs to one engine and is discarded on exit, including
rejection or failure; it adds no public event fields or persistent game state.
Runtime-trust schema two binds current records to this capture contract.
Replay matches completion order and repeated occurrences before verifying each
source's registered or implicit binding. Missing, extra, reordered or unobserved
resolutions reject. Older records retain the existing historical-absence path;
declared runtime provenance must still match exactly.

```mermaid
sequenceDiagram
    participant Client
    participant Service as GameService
    participant Engine
    participant Store as Game Record v3
    Client->>Service: command envelope
    Service->>Engine: validate and apply transaction
    Engine-->>Service: events and resulting state
    Service->>Store: atomically persist record/idempotency
    Store-->>Service: durable
    Service-->>Client: accepted receipt and projection
```

Replay rebuilds from the initial checkpoint and applies the canonical accepted
commands under matching fingerprints. It compares authoritative state hashes
and fails on version, semantics, CardProgram trust, runtime binding, database,
command, or result divergence. Historical v3 records without the additive
trust/runtime fields keep their existing semantic-registry verification.
Projected delivery packets are transport evidence, not alternative authority.

Capabilities are never persisted in raw form. Private record artifacts remain
ignored local data and must not be committed. See the [Game Record contract](../reference/game-record.md)
and the [replay testing guide](../testing/replay.md).
