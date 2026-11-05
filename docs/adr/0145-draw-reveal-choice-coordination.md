---
title: "ADR 0145: draw reveal choice coordination"
status: "ADR"
authoritative_source: "typed drawing, source-linked reveal choices and canonical draw commits"
verified: "2026-10-30"
audience: "drawing, casting, visibility and replay contributors"
maintenance: "hand-maintained"
adr_id: "0145"
decision_status: "accepted"
date: "2026-10-30"
---

# ADR 0145: draw reveal choice coordination

## Context

Draw instructions, replacement choices and reveal-as-drawn choices share one
instruction trampoline. A battlefield source that reveals a drawn card is
distinct from an intrinsic ability of the card being drawn. The existing
battlefield source and continuation checks must retain that distinction.
Adding intrinsic handling directly to the instruction coordinator would mix
another decision protocol into its queue and replacement responsibilities.

## Decision

`drawing/reveal_coordination.py` owns battlefield reveal-policy discovery,
authenticated reveal/decline choices, exact source revalidation and completion.
`drawing/coordinator.py` retains the single queued-instruction trampoline and
replacement orchestration. It reexports the established reveal entry points.
Completion calls the canonical draw transaction and then resumes that same
trampoline; no callback is stored in a rule descriptor or continuation.

`drawing/transaction.py` remains the mutation owner for card movement, reveals,
draw history and draw events. Existing reveal payloads and battlefield-source
requirements remain unchanged. An intrinsic card variant requires its own
closed role and continuation; changing an active-zone flag or relaxing the
battlefield check does not implement it.

## Alternatives

Keeping reveal choices inside the instruction coordinator couples source
identity checks to queue traversal. A separate instruction loop for intrinsic
reveals duplicates ordering and resume authority. The extracted reveal owner
preserves the existing single trampoline and transaction contracts.

## Consequences

The reveal protocol can evolve alongside explicit prospective-card and hand
identities while queue progression stays in one owner. Deferred imports on
completion avoid a runtime import cycle between the two coordinators.
Existing public imports and recorded schema shapes remain available. The
extraction preserves the function ASTs apart from the explicit trampoline
import and is protected by mandatory-reveal and pending-replay witnesses.
