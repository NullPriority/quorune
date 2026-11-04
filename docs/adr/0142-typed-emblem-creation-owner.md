---
title: "ADR 0142: typed emblem creation owner"
status: "ADR"
authoritative_source: "emblem construction and command-zone object authority"
verified: "2026-10-30"
audience: "rules, compiler, command-zone and replay contributors"
maintenance: "hand-maintained"
adr_id: "0142"
decision_status: "accepted"
date: "2026-10-30"
---

# ADR 0142: typed emblem creation owner

## Context

CR 114 distinguishes an emblem from a card, token and permanent. Its abilities
function in the command zone, and the creating effect determines its owner and
controller. The existing engine constructor creates this public object for
historical emblem and Ring consumers, but owns its direct state writes.

Generic compiled emblem abilities need a stable object boundary. They cannot
derive executable behavior from the emblem's display text or continue to use
the creating permanent as the enduring source.

## Decision

Move emblem construction into `quorune/emblems.py`, a protected rules-layer
mutation owner with a narrow structural host protocol. Prepare one immutable
creation value before allocating a reference or timestamp. Commit revalidates
the player, creates exactly one noncard command object, and owns its zone entry,
object map, compatibility marker and public creation journal. The engine keeps
its existing constructor signature as a delegating facade.

Declare the shared `object.emblem.creation` capability over this owner. Existing
Ring temptation depends on it, so withholding construction authority blocks
that instruction without disabling unrelated spell behavior.

Preserve the existing normalization, reference and logical identity creation,
timestamps, display-only annotations, optional legacy semantic reference and
journal fields. Existing Ring and historical emblem dispatch keep their
explicit compatibility ownership. The new owner evaluates no Oracle prose,
dispatches on no card identity, and adds no registry or executable callback.

This constructor extraction does not admit generic compiled emblem abilities.
Their later compiler/runtime integration must bind independently closed child
programs to the actual command-zone emblem, preserve typed capability minima,
and retain unknown or unsupported children as parent residuals.

## Alternatives

- Grow the engine constructor with new rules and child-program dispatch. This
  retains direct mutation debt and mixes object creation with ability execution.
- Model emblems as battlefield tokens. This applies the wrong type, targeting,
  lifetime and zone rules.
- Interpret stored display text during gameplay. This creates an unreviewed
  executable authority and prevents exact typed replay.

## Consequences

The rules owner validates before mutation, while ordinary object serialization,
projection, stable identity and command-zone consumers remain shared. Focused
evidence compares the extracted constructor with its previous implementation
and verifies public, persistent nonpermanent behavior through the engine facade.
Malformed and stale-player requests leave allocation counters, objects, zone
membership and journals unchanged. The engine's direct mutation surface shrinks.

## Removal condition

Replace this owner only with a boundary preserving validation before allocation,
owner/controller identity, command-zone membership, noncard object kind,
historical serialization and journals, projection, and exact replay. Any future
compiled ability support must retain its own explicit trust and child-program
validation rather than making display text executable.
