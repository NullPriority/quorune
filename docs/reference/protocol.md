---
title: "Application protocol"
status: "current"
authoritative_source: "GameService, server OpenAPI, protocol implementation, and versioned schemas"
verified: "2026-08-25"
audience: "client, server, and protocol contributors"
maintenance: "hand-maintained"
concern: "network-protocol"
---

# Application protocol

Every browser, CLI, scripted pilot, subprocess, and optional automated client
uses the same projected-state and capability-command boundary. A client can
render richer presentation or strategy, but it cannot select its principal,
read authoritative state, or mutate game fields directly.

The generated [protocol inventory](protocol-inventory.md) lists the current
OpenAPI operations and versioned JSON schemas. Generate it from the application
and schema files rather than copying a route table into hand-maintained prose.

## Transport-neutral boundary

`GameService` exposes observation and command operations:

```python
service.observe(authenticated_principal, full=False)
service.command(CommandEnvelope(...), principal=authenticated_principal)
```

The transport authenticates a guest, binds room membership to one principal,
forwards observations and commands, and owns connection-local projection
cursors. `principal` is trusted transport metadata, never request content. A
room spectator maps to the capability-free `spectator` principal.

HTTP owns request/response lifecycle operations. The game WebSocket carries
the same principal-scoped full and delta packets plus safe lifecycle metadata.
The in-process and network paths share protocol and validation code; neither is
a second rules interface.

## Command envelope

[`schemas/command-envelope.schema.json`](../../schemas/command-envelope.schema.json)
is the network command authority. A command supplies:

- the protocol and game identity;
- a client command idempotency key;
- the current decision and server-issued action IDs;
- one opaque, single-use capability;
- the expected projected-view revision; and
- only the delegated choices declared by that action.

The envelope cannot select a principal, seat, controller, effect operation,
mana side effect, state field, or unadvertised cost. The service validates the
strict field set, game, expected revision, identity, capability, decision,
action, choices, legality, costs, and targets before mutation. A rejected
attempt rolls back and does not consume the decision capability. Reusing a
client command ID with a byte-equivalent request returns the durable receipt;
reusing it for different content is a conflict.

## Projected packets

[`schemas/decision-packet.schema.json`](../../schemas/decision-packet.schema.json)
defines the client packet. A full packet establishes a principal-specific view,
its canonical hash, current revision, visible definitions/events, and current
decision. A delta names the exact base hash, resulting view hash, patch, event
tail, and current decision.

A client must:

1. apply a delta only when its `base` equals the local view hash;
2. verify the resulting canonical `view` hash;
3. request or await a new full packet after a mismatch or reconnect;
4. replace a stale decision when the packet carries `decision: null`; and
5. submit only the action IDs and choice fields issued by the current decision.

Every network connection has an independent ephemeral cursor. Reconnect begins
with a full packet, so cursor state is an optimization rather than replay or
correctness authority. The Python `ProjectedClientView` implements the same
rules for in-process clients.

Public battlefield objects include compact `regen` only while one or more
regeneration shields exist. The omitted value means zero. It is projection of
authoritative logical-object state, not a client-side effect prediction.

## Actions and choice forms

Legal actions contain stable action IDs and may contain a versioned JSON choice
form. The same adapter that projects a form defines the accepted choice-field
names. Forms cover scalar, object, ordering, mode, target, grouped, assignment,
payment, private search, and delegated rules choices. Unknown fields and
unknown form versions fail closed.

Modal submissions are closed nonrepeating sets. The server validates their
minimum, maximum, and membership, then canonicalizes every legal set into the
printed order before target planning, stack construction, and execution. The
browser mirrors that order from the server-issued schema and retains target
groups that remain legal when the selected set changes.

The client may choose presentation and automation policy. For example, an
automatic pass still submits the ordinary server-issued `pass` action, and
automatic mana still selects from authoritative mana modes. The engine
revalidates the result; presentation code never predicts legality.

## Lifecycle and public history

Safe game inspection, administrative stop/resume, command submission,
projection reads, public-event pagination, and streaming all cross the same
per-game actor. An accepted mutation is persisted before acknowledgement.
Paused, corrupt, aborted, and complete games expose no reusable player action.
Administrative resume can clear only its own stop reason; it cannot override a
rules, fidelity, corruption, or terminal boundary.

The WebSocket event tail is bounded delivery context. The paginated public log
returns a fixed spectator-filtered event shape and never exposes raw details,
checkpoints, private events, capabilities, record paths, or analyst artifacts.

## Versioned schema ownership

- [Command envelope](../../schemas/command-envelope.schema.json) — authenticated
  command input
- [Decision packet](../../schemas/decision-packet.schema.json) — projected full
  and delta output
- [Pilot response](../../schemas/pilot-response.schema.json) — optional
  provider response before it is normalized to a command
- [CardProgram](../../schemas/card-program-v2.schema.json) — compiled card
  behavior, not a client mutation format
- [Game Record schemas](game-record.md) — durable replay and audit files, not a
  network API

Browser TypeScript bindings are generated from the command and decision schemas
with `npm run generate:types --prefix web`. OpenAPI owns HTTP models and status
codes. Change a schema and its producers, consumers, compatibility tests,
generated bindings, protocol inventory, and reference in one coherent change.

## Security and extension rules

- Derive identity from the authenticated connection; reject identity in bodies.
- Project before serialization and independently filter public-history output.
- Treat capabilities as narrow decision grants, never login credentials.
- Keep full checkpoints, raw journals, private zones, provider memory, and
  analyst artifacts outside every client route.
- Rate-limit hostile transport traffic without changing engine semantics.
- Add positive and negative projection, idempotency, replay, malformed-input,
  and reconnect tests for new fields or operations.

See [visibility](../architecture/visibility.md),
[server runtime](../architecture/server-runtime.md),
[replay](../architecture/replay.md), and the
[threat model](../THREAT_MODEL.md).
