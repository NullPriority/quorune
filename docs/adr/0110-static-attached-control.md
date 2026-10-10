---
title: "ADR 0110: static attached control"
status: "ADR"
authoritative_source: "quorune/attached_control.py, control_effects.py, and semantic_runtime/attached_control.py"
verified: "2026-10-10"
audience: "compiler, rules, replay, and architecture contributors"
maintenance: "hand-maintained"
adr_id: "0110"
decision_status: "accepted"
date: "2026-10-10"
---

# ADR 0110: static attached control

## Context

An Aura that controls its enchanted object creates a continuous layer-two
effect. The Aura's controller and the recipient's owner are independent.
Attachment supplies the timestamp and current logical relationship. Changing
the Aura's controller changes its control effect; a later independent control
effect can still supersede it.

## Decision

Compile the unconditional enchanted-permanent control instruction into one
closed runtime descriptor in the existing continuous-component registry.
Its pure handler lowers the current reciprocal attachment and source
controller into a static layer-two effect. Source incarnation distinguishes
multiple copies of the same program.

The existing control owner retains initial custody before the first static
control acquisition and commits final controllers through its canonical
custody, combat, and acquisition-history operation. The read-only planner
combines static relationships with the same resolution-control journal.
An Aura effect depends on effects that change its source's controller; the
existing dependency and timestamp order resolves chains and cycles. Initial
custody supplies the calculation's starting state on every stabilization.

Intrinsic applicability is evaluated at layer two, before later ability-removal
effects. A layer-six ability removal does not undo control already applied in
layer two. Removing the source or changing the attachment removes the static
effect. Phased-out objects preserve custody and acquisition history until
they phase in.
No new state schema or competing control registry is introduced.

## Alternatives

A one-time gain-control instruction would outlive its attachment and use the
wrong timestamp. Restoring to the owner would lose preexisting custody. A new
controller journal would duplicate resolution and source-duration ownership.
Runtime Oracle parsing or card identity dispatch cannot establish authority.

## Consequences

Unconditional ordinary enchanted-permanent subjects are represented.
Conditional control and player scopes remain residuals. Unsupported sibling
abilities continue to prevent whole-card admission. Historical provenance and
current typed control-history execution retain their separate contracts.

## Verification

Actual casts cover acquisition, controller-owned commands, initial custody,
source bounce, competing Auras and resolved control, source-controller chains,
and returning recipients. Typed diagnostics distinguish dependency cycles,
phasing, and later-layer ability loss without a zone change. Replay and pending restoration
remain subject to existing runtime trust and protected certification.
