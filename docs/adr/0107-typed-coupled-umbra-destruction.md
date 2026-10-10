---
title: "ADR 0107: typed coupled Umbra destruction replacement"
status: "ADR"
authoritative_source: "quorune/destruction.py and the typed destruction replacement modules"
verified: "2026-10-10"
audience: "rules, compiler, replay, and architecture contributors"
maintenance: "hand-maintained"
adr_id: "0107"
decision_status: "accepted"
date: "2026-10-10"
---

# ADR 0107: typed coupled Umbra destruction replacement

## Context

The destruction transaction from ADR 0027 and regeneration effects from
ADR 0084 did not represent Aura-owned Umbra armor. They also rejected an
ordinary choice between shield and regeneration replacements. Umbra armor
replaces destruction with two coupled instructions: clear all damage from the
enchanted permanent and destroy the chosen Aura. The replacement belongs to
the Aura, while the enchanted permanent's controller chooses among applicable
protections. Simultaneous destruction and pending choices require one stable
source, attachment and incarnation boundary.

## Decision

Represent Umbra armor as a closed source-local `UmbraArmorSpec` in the existing
ability-fragment and layer-6 owner. Read current Aura/recipient relationships
through `umbra_armor.py`; losing recipient abilities does not remove an
external Aura's protection. Compiler recognition declares both representation
and complete destruction capabilities. A recognized fragment alone never
certifies gameplay support.

Prepare immutable destruction subjects and use the existing replacement
batch planner for controller-correct Umbra, regeneration and shield choices.
`destruction_replacement_planning.py` expands the selected Aura destruction,
retains decisions, deduplicates a simultaneous original Aura request and
bounds a recursive Aura chain under CR 614.5. `destruction_replacement_adapter.py`
binds current snapshots and reconstructs them before commit. Old single-owner
dispositions reuse the same typed subject validation.

The existing destruction transaction remains the mutation owner. It clears
protected damage through `damage_results.clear_permanent_damage` before the
replacement Aura's zone movement, uses the canonical simultaneous zone owner,
and consumes represented shield or regeneration resources through their
existing owners. Umbra does not tap the recipient or remove it from combat.
Indestructible, non-destruction graveyard moves and regeneration prohibition
retain their distinct meanings.

Effect intent identities use a closed destruction continuation codec. Lethal
and deathtouch state-based actions use a separately typed
`state_based_destruction` replacement continuation. Its sealed frame binds the
current battlefield, stack, turn, source abilities, damage, resources and
attachments. The authenticated chooser sees the replacement options; other
principals do not. Synchronous resume selections are scoped by the coordinator
and never become an unrecorded engine field. The engine delegates preparation
and keeps the existing stabilization loop; ordinary counter logging moves to
the rules owner without changing its public event data.

No card identity dispatch, runtime Oracle parsing, executable callback, second
registry or new generic effect opcode is introduced. The new replacement event
vocabulary permits only the declared disposition, damage-clear and Aura
identity fields through the existing typed SetField operation. Existing
continuation shapes retain their historical defaults.

## Evidence and limits

## Consequences

Focused evidence includes a trusted Hyena Umbra cast and attachment, a
supported destruction spell, competing Auras controlled by other seats,
pending save/load and exact replay, simultaneous Aura/recipient destruction,
lethal and deathtouch stabilization, forbidden regeneration, indestructible
Aura and recipient distinctions, stale attachment/ability/choice rejection,
resource choice, recursive planning and an independently killed missing-clear
mutation. Whole-card admission still requires every other Oracle sibling to
close. Current pinned corpus and generated freshness remain the delivery
authority; this decision does not declare universal match readiness.

## Alternatives

A fixed protection precedence would violate the affected controller's choice.
Moving the recipient to the graveyard and returning it would change object
identity and event history. Treating Umbra as regeneration would incorrectly
tap and remove it from combat. Each is rejected in favor of the shared typed
replacement and mutation owners.
