---
title: "ADR 0109: typed trigger usage limits"
status: "ADR"
authoritative_source: "quorune/rules/trigger_limits.py, activation_usage.py, and trigger_discovery.py"
verified: "2026-10-10"
audience: "compiler, rules, replay, and architecture contributors"
maintenance: "hand-maintained"
adr_id: "0109"
decision_status: "accepted"
date: "2026-10-10"
---

# ADR 0109: typed trigger usage limits

## Context

A printed trigger that triggers only once each turn consumes its allowance
when it triggers. Resolving, countering, or copying the stack ability does not
change that allowance. Additional-trigger effects must respect the limit.
Control changes and temporary ability loss preserve the same object's use;
a zone change creates a new object.

## Decision

Compose a closed versioned `trigger_limit` descriptor with an independently
exact battlefield event and result. Preserve the full original source span and
require the usage capability separately from event and result capabilities.
The descriptor is optional in both SemanticProgram and CardProgram runtime
codecs. Older payloads retain their original field set and hashes.

Extend the existing per-object ability-usage owner with a separate triggered
ability journal. The journal keys intrinsic compiled ability identities and
records the global turn sequence. Trigger discovery checks current ability
applicability and event conditions before consuming usage, then suppresses
additional-trigger multiplication. Stack copies retain ordinary copy behavior.
The canonical zone-change reset clears usage for a returning object.

## Alternatives

Counting resolved stack items would allow repeated triggers while an earlier
one is pending or countered. A controller journal would reset incorrectly on
control changes. A second event-history registry would duplicate ownership.
Parsing a limit from Oracle text during discovery would violate the compiled
runtime boundary.

## Consequences

Only the exact printed once-each-turn trigger suffix is represented. Action-
dependent limits, first-time event conditions, departure subscriptions,
nonbattlefield sources, and unrepresented granted abilities remain residuals.
Independent unsupported siblings continue to block whole-card admission.

## Verification

Focused evidence distinguishes two real counter events, separate objects,
blink, countered triggers, additional-trigger effects, ordinary stack copies,
control changes, later turns, and temporary ability loss. New descriptors,
current-turn usage, private commands, save/load, and pre-action replay remain
subject to the existing trust and certification gates.
