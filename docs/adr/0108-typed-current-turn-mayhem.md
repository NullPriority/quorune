---
title: "ADR 0108: typed current-turn Mayhem"
status: "ADR"
authoritative_source: "quorune/cast_lifecycles.py, compiled_cast_lifecycles.py, and canonical casting owners"
verified: "2026-10-10"
audience: "compiler, casting, replay, and architecture contributors"
maintenance: "hand-maintained"
adr_id: "0108"
decision_status: "accepted"
date: "2026-10-10"
---

# ADR 0108: typed current-turn Mayhem

## Context

Mayhem grants permission to cast or play an intrinsic card from its owner's
graveyard only after that player discarded the card during the current turn.
The costed form replaces the mana cost; the bare form retains ordinary spell
costs and land-play rules. A card moved to the graveyard by another instruction
or after another zone change has no authority from an earlier discard.

## Decision

Extend the existing cast-lifecycle descriptor with version four for fixed
ordinary-mana and bare Mayhem. The handler registry and source-span owner stay
shared with prior lifecycles. Versions one through three retain their original
serialization and validation. No second zone-casting registry is introduced.

The read-only permission query requires current complete-card admission,
current intrinsic applicability, owner custody, the graveyard, and a current
turn journal. It compares the current object's immediately preceding zone
incarnation with the sealed typed discard occurrence. A later zone move,
different actor, expired turn, absent journal or inexact source rejects the
permission. Offers and commit revalidation use that same query.

Fixed Mayhem supplies one alternative cost through the canonical total-cost
owner. Mandatory additional costs and cost modifiers still apply. Bare Mayhem
uses the printed spell-cost branch or normal land-play timing and quota.
Ordinary cast timing and resolution destinations remain unchanged. No runtime
path interprets Oracle text or dispatches on card identity.

## Alternatives

A new persistent card annotation would duplicate history authority and require
another cleanup and replay migration. Name or physical-card matching could
incorrectly preserve a permission after a zone change. A separate Mayhem cast
implementation would duplicate timing, pricing, targets and rollback.

## Consequences

The additive descriptor permits exact replay without rewriting older contracts.
Hybrid, variable, snow, Phyrexian and nonmana Mayhem costs remain explicit
residuals until their payment shape is represented and assured. Compiler
recognition never promotes an independently incomplete sibling ability.

## Verification

Focused evidence uses actual discard and Mayhem commands, current-turn and
incarnation counterexamples, stale-proposal rejection, mandatory additional
cost payment, land-play limits, ordinary versus Flash timing, replaced discard
destinations, descriptor round trips, mutation, privacy and pre-action replay.
Generated ownership and public certification remain mandatory for delivery.
