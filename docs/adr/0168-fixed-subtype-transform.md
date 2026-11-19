---
title: "ADR 0168: Fixed subtype transformation"
status: "ADR"
authoritative_source: "CR 205.3m, 701.27, 702.145 and the canonical transform batch owner"
verified: "2026-11-15"
audience: "compiler, rules and replay contributors"
maintenance: "hand-maintained"
adr_id: "0168"
decision_status: "accepted"
date: "2026-11-15"
---

# ADR 0168: Fixed subtype transformation

## Context

An instruction to transform all permanents of one creature type selects current
effective battlefield members when it resolves. It does not capture a source
incarnation or transform count when the spell is cast. Existing source abilities
have a separate captured-count rule, and transforming still requires the shared
eligibility, simultaneous timestamp and normalized event owner.

## Decision

Retain the existing `transform` operation and handler version. Add the explicit
`subtype_transform_version: 1` producer with one canonical `subtype` field. It
accepts no target, arbitrary query, relative type or source-count field. The
compiler accepts the complete unqualified fixed-type mass instruction.

The rules selector resolves effective public battlefield membership and passes
current transform counts to the existing canonical batch owner. Counts are
captured at this instruction, allowing separate spell resolutions to transform
the same still-matching permanent again. Eligibility, defined double-faced
objects, Daybound/Nightbound exclusions, event batching, timestamp choices and
atomic command rollback remain with their existing owners.

Timestamp-order continuation version two stores the suspended trusted stack
instruction and remaining effects for resolution. It validates the original
program, version, instruction, controller and current subjects before resuming
through the existing resolution entry point. Prior untap and state-action
continuations retain their exact version-one encoding and fields. The following
instruction waits until every controller timestamp order and face change commits.

An independent fine capability is inferred from the marker without relying on
coverage hints. Unknown marker versions or fields fail closed. Unmarked saved
source-transform instructions keep their prior encoding and semantics. New
records retain the marker, so an older reader cannot silently interpret the
instruction as a source transform.

## Consequences

Mass subtype selection composes with independently closed following instructions
in printed order. No runtime Oracle text or card identity controls selection.
Targeted transformation, front-face-only selectors, unions, chosen or relative
types and Convert require separate represented producers. Current prevention
scopes continue to inspect damage-time subtype facts rather than this transform
membership snapshot.

## Alternatives

- Capturing source or member counts when a spell is cast would incorrectly bind
  resolution-time membership and separate spell resolutions.
- Reusing an arbitrary object query in the persisted instruction would admit
  targets, unions and relative selections without their own bounded contract.
- A separate transform mutator would duplicate face eligibility, timestamps,
  normalized events and historical replay behavior.

Fixed-subtype transformation currently requires a direct resolution instruction.
Nested optional, conditional, payment and modal carriers fail runtime admission
until their timestamp suspension has a represented continuation.
