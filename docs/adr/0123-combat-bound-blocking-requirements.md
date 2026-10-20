---
title: "ADR 0123: combat-bound blocking requirements"
status: "ADR"
authoritative_source: "keyword event programs, CombatState and declaration requirement owners"
verified: "2026-10-20"
audience: "rules, compiler, replay and architecture contributors"
maintenance: "hand-maintained"
adr_id: "0123"
decision_status: "accepted"
date: "2026-10-20"
---

# ADR 0123: combat-bound blocking requirements

## Context

CR 702.39 makes Provoke an optional targeted attack trigger. The defending
player's tapped creatures are legal targets. Choosing to apply its effect
requires the target to block that attacker this combat if able, then untaps it.
CR 509.1c maximizes compatible requirements without violating restrictions or
forcing optional blocking payments. A resolved requirement remains effective
after ability loss but cannot follow a returned incarnation or another combat.

## Decision

Extend the existing source-spanned keyword event production with a defending-
player creature target and the existing optional-effect choice. Bind the
existing exact controller-seat target field to the attack event's defending
player. Ordinary target selection and resolution revalidation share that
schema, including when another opponent controls a potential target.

Review one generic `require_blocking_this_combat` operation in the existing
declaration effect family. The narrow rules coordinator delegates an immutable
`ResolvedBlockingRequirement` to the existing combat relationship mutation
owner and untapping to the existing replacement-aware tap-state owner. The
instruction accepts one attacker, one blocker and an explicit untap flag.
It does not waive evasion, tapped state, capacity or declaration prices.

Store exact attacker and blocker incarnations in the current `CombatState`.
The existing declaration projection converts current relations into ordinary
`choose_option` requirements for the existing solver. Combat reset expires
them. No ability fragment or source keyword needs to survive resolution.
Source departure prevents an effective requirement while preserving the
independent target untap when the optional result is chosen.

Runtime binding independently requires the correlated target, optional result,
untap, requirement and Provoke capabilities. Malformed shapes and undeclared
minimum dependencies reject. Empty requirement books retain historical combat
payload bytes; present books use a closed version-one codec.

The exact architecture binding adds only this operation. Existing engine,
direct-write, runtime-text, identity and size ceilings retain their values.
There is no size-growth exception, new solver or duplicate registry.

## Alternatives

- A permanent ability grant would end incorrectly after ability removal.
- A turn duration could leak into an additional combat.
- Forcing a blocker outside declaration solving could violate restrictions or
  another requirement.
- Direct untapping would bypass Stun replacement.

## Consequences

Native witnesses cover complete printed bodies, decline, targeting another
opponent, conflicting requirements, Stun, flying restrictions, blink, pending
load, replay, private projection and command rollback. Fresh witnesses omit
actual trigger production, requirement commitment and untapping separately.
The complete-body check also requires the fixed self-prevention production to
supply the existing canonical source predicate to the prevention shield owner.

## Removal condition

Retire this binding when the operation is removed or a later exact review
supersedes it. A replacement must retain optional resolution, target timing,
restriction priority, original incarnations, combat expiry and replay privacy.
