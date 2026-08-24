---
title: "ADR 0082: typed fixed nonrepeating modal programs"
status: "ADR"
authoritative_source: "fixed modal compiler, capability shape, and canonical spell, trigger, activation, targeting, and resolution owners"
verified: "2026-08-24"
audience: "compiler, rules, runtime, assurance, and architecture contributors"
maintenance: "hand-maintained"
adr_id: "0082"
decision_status: "accepted"
date: "2026-08-24"
---

# ADR 0082: typed fixed nonrepeating modal programs

## Context

ADR 0071 deliberately admitted only complete two- or three-mode `Choose one`
instant and sorcery faces. Subsequent typed effect harvests closed a larger
coherent cohort: fixed nonrepeating modal selections whose branches are already
independently exact across whole spells, normalized triggers, and supported
activated abilities. The pinned cohort contains 65 programs on 64 complete
Commander cards, 156 closed branches, 65 exact abilities, and 221 removable
material residuals.

The existing target model already represents bounded mode counts and rejects
duplicate modes. It did not, however, provide stable cross-mode target-group
identities, enforce printed mode order, or rebase branch-local target references
when more than one selected branch resolves.

## Decision

Add a separate `choice.modal.fixed_nonrepeating` capability and
`fixed-nonrepeating-modal` mechanic. Preserve ADR 0071's strict compiler,
capability, and serialized output for previously exact ordinary `Choose one`
spells.

The new compiler accepts one complete source-spanned modal block with two through
five bullet modes and exactly one of these selection profiles:

- `Choose one`;
- `Choose one or both` for two modes;
- `Choose one or more`; or
- `Choose two`.

Every branch must already lower through an independently capability-closed typed
effect owner. Each targeted branch receives a mode-qualified target-group ID.
The shared target owner requires distinct mode IDs in printed order, publishes
only modes whose targets are currently feasible, and withholds the action when
fewer than the minimum number of modes remain legal. Resolution rebases each
branch's local target references over the selected target groups before running
the existing effect dispatcher.

Whole spells use the modal template directly. Triggered and activated blocks
compose the same template into their existing normalized event and typed cost
owners; they do not introduce another stack, event, activation, choice, target,
or effect runtime. Capability reconstruction requires the exact union of the
modal capability, every child branch, and any normalized trigger wrapper.

Repeatable and random modes, conditional selection counts, Entwine, Escalate,
Spree, selection-history tracking, cross-mode target constraints, unsupported
event bindings or activation costs, and branches outside existing typed owners
remain source-spanned residuals.

This supersedes ADR 0071 only as the owner for newly admitted nonrepeating modal
profiles and nonspell contexts. ADR 0071 remains the compatibility contract for
its existing strict output.

## Alternatives

- Widen `choice.modal.fixed_one` in place. Rejected because its capability ID,
  template shape, and accepted ADR explicitly certify exactly one mode on a
  complete ordinary spell.
- Compile each bullet as an independent node. Rejected because unselected modes
  would become executable and announcement-time target commitment would be lost.
- Add a modal-only runtime dispatcher. Rejected because mode selection, target
  validation, stack placement, and typed effect execution already have shared
  owners.
- Include modal additional-cost and repeatable-mode mechanics. Rejected because
  they require distinct casting-cost or selection-state authority.

## Consequences

- One compiler grammar fans into spell, triggered, and activated contexts.
- Multi-mode targets keep stable group identity and branch-local effect meaning.
- Existing strict `Choose one` programs retain byte-stable template ownership.
- New modal shapes fail closed unless every branch and wrapper dependency is
  trusted.
- Behavioral evidence covers multi-mode rollback, privacy, printed-order
  resolution, activation costs, normalized trigger targeting, and replay.

## Removal condition

Retain this decision until a more general typed modal declaration model can
represent selection-linked costs, repetition, conditional counts, selection
history, and cross-mode target relations while preserving the same fail-closed
child-capability and runtime boundaries.
