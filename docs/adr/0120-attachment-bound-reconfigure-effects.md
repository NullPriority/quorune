---
title: "ADR 0120: attachment-bound Reconfigure effects"
status: "ADR"
authoritative_source: "source-spanned activation catalog, attachment owner and continuous-effect journal"
verified: "2026-10-20"
audience: "rules, compiler, replay and architecture contributors"
maintenance: "hand-maintained"
adr_id: "0120"
decision_status: "accepted"
date: "2026-10-20"
---

# ADR 0120: attachment-bound Reconfigure effects

## Context

CR 702.151 defines two separately paid sorcery-speed abilities. Attaching an
Equipment with Reconfigure creates a type-changing effect until it becomes
unattached from that creature. Removing its abilities does not end this effect.
A static predicate checking whether the Equipment currently has Reconfigure
would restore its creature type at the wrong time. Later animation can still
make it a creature and cause ordinary state-based unattachment.

## Decision

Lower fixed ordinary-mana Reconfigure into two source-spanned programs using
the existing activation catalog and complete-card admission. An optional typed
Reconfigure marker on each activation identifies the represented keyword;
current layered ability discovery determines its presence at attachment.
Offers and commands share the existing sorcery, mana, target and attached-source
condition owners. Reconfigure has no Equip semantic identity or tap cost.

The common Equipment attachment boundary applies the rule consequence for any
represented attachment instruction. It delegates reciprocal mutation to
`attach_objects` and commits a layer-4 `remove_creature_card_type` operation
through the existing continuous-effect journal. The effect records both
incarnations and the changed attachment timestamp. Live applicability checks
the original reciprocal relation. Control changes and phasing preserve that
relation; departure or later attachment cannot revive the old effect.

Review one new universal `unattach` operation in the existing zone/attachment
effect family. It resolves the original source incarnation and delegates to
`detach_object`, preserving tapped state and timestamp. This operation accepts
no callbacks, zone movement, target substitution, or arbitrary state writes.
It is independently meaningful under CR 701.3d and does not create another
registry or mutation owner.

The exact architecture binding adds only this operation to the registered
operation allowance. Existing engine, direct-write, identity, runtime-text and
size ceilings retain their previous values. No size-growth exception is
authorized. Optional activation and duration facts leave older serialized
payloads unchanged when absent.

## Alternatives

- A live static keyword effect ends incorrectly after ability removal.
- A permanent annotation cannot describe the original receiver and lifetime.
- Treating Reconfigure as Equip applies the wrong timing and cost modifiers.
- A card-specific dispatcher or separate mechanic registry duplicates authority.
- Encoding unattachment as a zone move changes identity and unrelated state.

## Consequences

Native actions cover separate payments, target control and self-exclusion,
reattachment, same-target no-op, source taps, ability loss, later animation,
departure, pending load and pre-action replay. Owner checks distinguish
ordinary animated Equipment, control changes, phasing and new incarnations.
Closed codec and dependency tests retain unsupported costs as residuals.

## Removal condition

Retire the operation allowance when `unattach` is removed or a later exact
review supersedes this binding. A replacement must preserve separate paid
abilities, source and receiver continuity, timestamp ordering, type/subtype
layers, state-based stabilization, privacy and historical payload compatibility.
