---
title: "ADR 0122: paid-mana resolution and target information"
status: "ADR"
authoritative_source: "existing resolution condition, semantic value, target identity and departure owners"
verified: "2026-10-20"
audience: "rules, compiler, targeting, privacy and replay contributors"
maintenance: "hand-maintained"
adr_id: "0122"
decision_status: "accepted"
date: "2026-10-20"
---

# ADR 0122: paid-mana resolution and target information

## Context

Actual colored mana can select an additional result or replace a spell's
ordinary result. These are different programs: Slaying Fire deals three or
four damage, while Searing Barrage deals five to a creature and may add three
to that creature's controller. An ability-word label supplies no rules logic.

An added result follows its prefix. Rally for the Throne counts creatures
after creating its Humans; Unexplained Vision draws before Scry. Existing
target legality still determines whether the whole spell resolves.

## Decision

Extend the existing `apply_if_public_condition` instruction with schema six
for a paid-mana added result and schema seven for a fixed same-target
self-replacement. A closed `ManaSpentCondition` contains one concrete mana type
and a fixed positive threshold. Its typed scalar reference reads the original
spell's immutable payment fact. A spell copy supplies zero paid mana, while a
missing original-cast fact fails closed.

The existing semantic value resolver binds the scalar at the instruction
boundary. The existing choice registry automatically prepends the selected
result without a player choice or a new mutation owner. Earlier instructions
remain a prefix. A single represented controller-life quantity is evaluated
at that boundary, after the prefix; broader derived result sequences remain
outside this contract. Every branch retains its independent child capability
closure. Whole-spell assembly preserves the complete source span and one
announced target contract. Older condition payloads retain their exact shapes.

A target-controller information reference reads an already announced target;
it does not announce another target. The existing target identity snapshot
binds its physical object and zone-change counter. The value resolver reads
current effective control for that incarnation. The canonical zone owner's
existing atomic departure capture retains the controller before a referenced
object leaves. A returned incarnation cannot replace that information.
Unknown information cannot fall back to the caster, owner or damaged player.

The fixed creature-damage prefix and information-derived player-damage result
have explicit correlated shape validation and runtime capability minima.
Their source attribution and damage commitment use the existing damage owner.
Initial target invalidity still prevents the whole spell from resolving.

No second condition or player-binding engine, component registry, universal
operation, engine method, runtime Oracle parser or gameplay cache is added.

## Alternatives

- Count printed pips, available mana or distinct colors: none is actual units.
- Treat an added result as a replacement: loses required prefix consequences.
- Apply default and replacement results together: deals seven instead of four.
- Evaluate a live quantity before the prefix: misses newly created creatures.
- Bind a controller at targeting time: misses later control changes.
- Follow a returned physical card: substitutes another rules object.
- Add a conditional player target: changes legality and opponent protections.

## Consequences

Normal original casts exercise two and three paid units, both result forms,
private draw/Scry, caster versus target recipients, copied unpaid results,
target departure, current control and preserved last-known information.
Pending load and pre-action replay protect the selected effects and private
choices. Typed codecs, verified registry withdrawals and fresh actual producer,
reader and departure omissions protect the source and trust boundaries.

## Removal condition

A replacement must preserve actual payment provenance, ordered result timing,
whole-spell target revalidation, correlated target roles, current/LKI identity,
private choices, legacy condition payloads and exact replay.
