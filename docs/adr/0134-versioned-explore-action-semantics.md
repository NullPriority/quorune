---
title: "ADR 0134: versioned Explore action semantics"
status: "ADR"
authoritative_source: "Explore preparation, state mode and Game Record provenance"
verified: "2026-10-28"
audience: "rules, persistence and replay contributors"
maintenance: "hand-maintained"
adr_id: "0134"
decision_status: "accepted"
date: "2026-10-28"
---

# ADR 0134: versioned Explore action semantics

## Context

CR 701.44a puts a counter on a current explorer whenever no land card is
revealed, including an empty library. The former empty-library branch recorded
completion without attempting that counter. Explore is reachable through
compiled instructions and predefined Map activations, so correcting only a
compiler descriptor leaves a second gameplay path incorrect. Existing records
bind their transition results and hashes, including the earlier empty result.

## Decision

New GameState instances use `explore_action_version: 1`. The immutable choice
query carries that mode. For an empty library, a current explorer first receives
the same replacement-aware counter intent used for a revealed nonland, then
records completion. A departed, phased or returned incarnation receives no
counter. Existing preparation and continuation owners resume suspended counter
replacement before completion without repeating either operation.

The current mode also retains source departure information through independently
owned optional-effect and payment wrappers. Source Explore uses its current or
last-known controller and original logical identity. Source pronouns are bound
only after the compiler proves a self event; another event creature is not
substituted with the observing source.

Keep existing Explore opcodes and Map token/builtin schemas. Mirror the mode in
Game Record format provenance and require it to agree with the initial
checkpoint. Historical absence remains absent on serialization and preserves
the former empty-library and source-capture semantics. Unknown or boolean
versions reject. This follows the additive provenance approach in
[ADR 0130](0130-casting-source-placement-before-costs.md).

## Alternatives

An unconditional change alters old record hashes. A compiler-only instruction
version misses unchanged builtins and requires parallel token-profile versions.
A second Explore mutation path duplicates counter replacement and continuation
authority. Treating empty completion as no exploration contradicts CR 701.44b.

## Consequences

Current games perform the complete action through one owner, while retained
unversioned states keep an explicit historical mode. No gameplay command changes
the mode. Counter permission, replacement ordering, actor projection, source
departure, snapshot hydration and exact replay remain required boundaries.
