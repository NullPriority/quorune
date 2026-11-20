---
title: "ADR 0170: Live direct player attack counts"
status: "ADR"
authoritative_source: "CR 109.5, 506.4, 508.1b, 508.3e and 608.2h"
verified: "2026-11-16"
audience: "compiler, quantity and replay contributors"
maintenance: "hand-maintained"
adr_id: "0170"
decision_status: "accepted"
date: "2026-11-16"
---

# ADR 0170: Live direct player attack counts

## Context

A creature attacks a particular player, planeswalker or battle. An instruction
that counts creatures attacking its controller counts direct player attacks.
Attacking a permanent controlled or protected by that player does not satisfy
that condition. Membership is current when the instruction applies.

## Decision

Extend the existing resolution-only live battlefield quantity with explicit
inner version three and `attack_destination: controller_player`. Its query is
exactly current battlefield creatures with attacking state; its creature
controller relation is any and source exclusion is absent. This keeps the
attack destination separate from creature owner, controller and defending
permanent associations.

Use the canonical public battlefield count, filtering the actual attack
reference against the already locked resolving controller before ordinary
current effective creature matching. The compiler accepts fixed direct-player
attack wording through the existing amount and complete token/life producers.

The quantity owns its producer dependencies. The existing dependency walker
rederives the fine version-three capability from nested data without trusting
coverage hints. Unknown destinations, unions or arbitrary target player
references fail closed. Versions one and two keep their exact wire fields and
stored dataclass fields. No new mutation operation or registry is introduced.

## Alternatives

- Filtering creature controller would count the wrong objects and commonly
  exclude all enemy attackers.
- Mapping attacked permanents to their controller or protector would broaden
  direct-player wording to a different Oracle condition.
- Capturing attackers when a spell is cast would miss legal responses and
  later attacking entrants before resolution.
- Changing the old all-attacker query would alter existing card behavior and
  historical records.

## Consequences

Existing canonical token and life owners apply the resulting count. Token
keywords and independently closed following instructions remain material parts
of the complete original. Static layer quantities, arbitrary attack unions
and unrepresented siblings retain their existing support boundaries.
