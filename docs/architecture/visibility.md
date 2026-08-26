---
title: "Visibility and projection"
status: "current"
authoritative_source: "StateProjector, protocol schemas, public-log filtering, and privacy tests"
verified: "2026-08-25"
audience: "client, server, pilot, and security contributors"
maintenance: "hand-maintained"
---

# Visibility and projection

Authoritative state is never a client payload. `StateProjector` derives a full
view for one transport-authenticated principal; delta delivery compares only
projected views. A checkpoint may contain every hidden zone and pending rules
fact, so access to the record is categorically different from client
observation.

## Principal views

- A player sees their legally known private cards and choices plus public game
  information and their current capability.
- An opponent sees public objects and counts unless a rule explicitly makes
  private information known to that seat.
- A spectator sees the public table and public history with no decision or
  command capability.
- A coordinator receives only the bounded public/control context needed for
  lifecycle or scoped rules work.
- An analyst is an out-of-band postgame role and does not share a live client
  route.

Authentication fixes the principal before projection. Request content cannot
promote a spectator, select another seat, or request an analyst view.

## Projection invariants

- Projection occurs before transport serialization, hashing, or patch
  generation.
- Raw capabilities, physical card IDs, incarnation/timestamp internals,
  authoritative continuations, hidden event details, private provider memory,
  and analyst artifacts do not enter another principal's view.
- Reconnect begins with a full projection and independent connection cursor.
- A delta applies only to its exact projected base hash.
- Private choice candidates appear only in the chooser's decision form.
- A face-down public-zone object's controller can see its identity. Ownership
  alone grants no visibility; other seats need explicit known or revealed
  state.
- Public exile is projected as a stable unordered set for live principals.
  Its presentation order is independent of the order cards left a hidden
  library; graveyard order remains authoritative because rules can use it.
- Public fields such as battlefield objects, commander damage, terminal result,
  and lifecycle status are consistent across permitted principals.

## Public history and images

Live event tails are bounded transport context. Complete public history is read
through the game actor and filtered with spectator visibility. The response
contains a fixed compact event shape; raw event details, checkpoints,
capabilities, record paths, private draws/searches, and analyst files are never
returned.

Projected cards carry only the short identity needed for presentation. The
same-origin image route resolves it against local metadata. Because opposing
hidden cards never enter a view, they also never enter another seat's DOM or
image-request working set.

## Extension rule

Every new state field, choice, event, zone, semantic node, protocol schema, or
server endpoint must declare visibility for owner, controller, opponent,
spectator, coordinator, and analyst contexts. Add positive and negative tests
for full projection, delta projection, reconnect, public history, persistence,
and malformed/forged identity where applicable.

Do not solve a client feature by exposing a checkpoint, broadening analyst
access, returning raw event details, or copying private information into a
public substitute field. See the [privacy testing guide](../testing/privacy.md),
[protocol reference](../reference/protocol.md), and
[threat model](../THREAT_MODEL.md).
