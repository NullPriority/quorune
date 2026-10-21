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

Target-dependent casting offers may include `target_costs` on a cost option and
its target form field. Public clauses identify the current source components
and matching target references. Each payable price row gives the selected mode
set, applicable component set, exact mana requirements and display label.
Multiple matching targets charge one component once. The browser selects a
display row as targets change and flags an absent payable combination. This
metadata grants no command fields; clients submit the ordinary cost option,
targets and modes, and the server determines the actual total cost again.

Prepared battlefield objects project `prepared: true`; ordinary objects omit
the field. Their inset spell copies are public exile objects with their own
references and inset characteristics. Casting actions appear only for the
prepared permanent's current controller. The client submits an ordinary cast
action; it never supplies a prepared designation, lifetime binding, or source
controller.

The `combat.attackers` response may include an optional `enlist` mapping from
declared attacker references to eligible nonattacking creature references.
Multiple Enlist instances use a distinct donor list. The issued choice form
publishes current options, source assignments and distinct-use constraints;
the server revalidates the entire declaration before tapping any donor.

The `combat.blockers` response maps each blocker reference to one attacker
reference or a list of distinct attacker references. Server-issued declaration
capacities and choice-form row limits describe the allowed count. Every
pairing must remain legal at submission; an ordinary creature retains capacity
one. Existing single-reference payloads preserve their representation.

Every network connection has an independent ephemeral cursor. Reconnect begins
with a full packet, so cursor state is an optimization rather than replay or
correctness authority. The Python `ProjectedClientView` implements the same
rules for in-process clients.

Public battlefield objects include compact `regen` only while one or more
regeneration shields exist. The omitted value means zero. It is projection of
authoritative logical-object state, not a client-side effect prediction.

## Actions and choice forms

Casting actions with an intrinsic next-cleanup timing consequence expose
`timing_permission` through their issued choice form when that choice matters.
The `intrinsic` value uses the card's timing permission and creates its cleanup
consequence; `normal` uses an independent permission. The server supplies legal
values and labels, selects an available independent permission by default, and
revalidates the choice with the ordinary cost, target, origin and casting
prohibition checks. This field does not select or replace a mana-cost option.

Legal actions contain stable action IDs and may contain a versioned JSON choice
form. The same adapter that projects a form defines the accepted choice-field
names. Forms cover scalar, object, ordering, mode, target, grouped, assignment,
payment, private search, and delegated rules choices. Unknown fields and
unknown form versions fail closed.

Dynamic `mana_modes` fields declare `allowed_colors`, an integral `total`, and
`single_color` instead of enumerating allocations. Clients accept only objects
with allowed color keys, nonnegative integral values, that exact total, and at
most one positive color when required. A zero total permits an empty object or
allowed zero entries. The server validates the same allocation contract after
costs; enumerated mana choices retain their existing option-membership check.
For a source-counter quantity with a counter-removal price, the advertised
total uses a read-only preview of the canonical exact payment plan. It commits
no counters or events; resolution still samples the amount after actual payment.
Source-sacrifice quantities retain their established predeparture information.

Object reference fields may declare `allowed_cardinalities` as a sorted set of
permitted selection counts within their minimum and maximum. An optional fixed
cost can therefore expose decline or complete payment without advertising the
intervening counts. Absence retains the historical continuous range. Clients
validate this constraint and distinct references; authoritative completion still
revalidates the full cost and current object identities.

Modal submissions are closed nonrepeating sets. The server validates their
minimum, maximum, and membership, then canonicalizes every legal set into the
printed order before target planning, stack construction, and execution. The
browser mirrors that order from the server-issued schema and retains target
groups that remain legal when the selected set changes.
Priced modal selections also bind the exact server-issued cost option. Entwine
offers one ordinary mode or all modes with its additional cost; a cheap
one-mode option cannot authorize an all-mode submission.

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
