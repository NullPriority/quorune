---
title: "Server runtime"
status: "current"
authoritative_source: "server package, GameService, ServerStore, and GameActor"
verified: "2026-08-06"
audience: "server and persistence contributors"
maintenance: "hand-maintained"
---

# Server runtime

The FastAPI application is a transport and local-operations adapter around the
deterministic game service. It owns guest authentication, room membership,
deck intake, HTTP/WebSocket delivery, one serialized actor per loaded game,
SQLite control-plane persistence, managed card snapshots, local images, and
static browser assets. Transport dependencies do not enter
`quorune/`.

## Lifecycle and ownership

`ServerStore` owns durable guests, token/invite hashes, memberships, seats,
rooms, deck readiness, game index, lifecycle metadata, and idempotency receipts.
It does not become game-state authority. Each loaded game has exactly one
`GameActor`; observations, commands, public-log reads, lifecycle mutations, and
cursor cleanup cross its bounded mailbox.

Only the actor invokes state-changing `GameService` operations. An accepted
command writes the Game Record and durable idempotency receipt before the
server acknowledges it. A stop writes a structured administrative pause before
replying. Retry with the same principal, command ID, and request fingerprint
returns the recorded receipt without applying the transition again.

## Identity and command boundary

Guest bearer tokens are random HttpOnly cookie values; SQLite retains hashes.
A per-tab selector binds distinct top-level tabs without putting the bearer in
JavaScript or URLs. Unsafe cookie-authenticated requests also require CSRF
proof. Room membership determines the player or spectator principal.

The browser supplies an action/capability, expected projection revision,
delegated choices, and client command ID. The server rejects client-selected
principal, seat, controller, mana side effects, effect operations, or state
fields. WebSockets carry projections and safe lifecycle metadata, never
checkpoints. See the [protocol reference](../reference/protocol.md) and
generated [operation inventory](../reference/protocol-inventory.md).

## Persistence boundary

SQLite is the application control plane. Game Record v3 is authoritative game
truth: initial state, checkpoints, accepted commands, events, decisions,
opportunities, semantic identity, manifest, and replay hashes. Projection
cursors are connection-local and nondurable. Capabilities are reissued after
load rather than persisted as raw secrets.

Routine saves reuse the persisted card-program snapshot when its current
program data and the saved file both match the previous write. A changed
program, missing snapshot, or restored older file triggers a fresh write.

Derived review files are not replay inputs. Routine live saves update the
authoritative record without rebuilding postgame review; pause, abort,
completion, explicit refresh, or finalization produces current derived review.
See [Game Record](../reference/game-record.md) and
[replay architecture](replay.md).

## Card-data and image services

The managed-data service builds a pending Scryfall SQLite snapshot beside the
active database and activates it only at a safe startup boundary. A game pins
its card metadata hash; lazy recovery opens the matching retained snapshot.
Unreferenced snapshots and superseded downloads are pruned.

The image route resolves a projected Oracle prefix against local metadata,
accepts only the pinned HTTPS Scryfall host, bounds the response, writes the
cache atomically, and serves the local copy thereafter. The client cannot name
an arbitrary upstream URL or enumerate the bulk database.

## Failure and recovery

- A rejected command leaves authoritative state and capability use unchanged.
- A lost response is recovered through durable idempotency.
- An ambiguous persistence failure fails the actor closed until durable reload.
- Reconnect starts a new cursor with a full hash-verified projection.
- Process restart lazily recreates the actor from the checksummed record and
  exact card-data snapshot. The server verifies the saved engine, semantic
  programs, and declared runtime trust before issuing player capabilities.
- Administrative resume clears only `administrative_stop`; rules, fidelity,
  corruption, abort, and completion remain fail-closed.
- Loss of authenticated game access terminates the stream instead of creating
  a reconnect loop.

## Deployment boundary

The topology is one local process and local storage. Production accounts,
password recovery, rate limiting, external actor leases, multi-process
ownership, PostgreSQL, reverse-proxy/TLS operations, secret rotation,
monitoring, backups, abuse controls, and public hosting are not implemented.
See [hosted operations](../operations/hosted.md), [security policy](../../SECURITY.md),
and the [threat model](../THREAT_MODEL.md).
