---
title: "ADR 0105: typed fixed resolution control and optional source untap"
status: "ADR"
authoritative_source: "control compiler, strict semantic handlers, continuous-effect journal, custody history and physical untap owners"
verified: "2026-10-06"
audience: "rules, compiler, replay, privacy and architecture contributors"
maintenance: "hand-maintained"
adr_id: "0105"
decision_status: "accepted"
date: "2026-10-06"
---

# ADR 0105: typed fixed resolution control and optional source untap

## Context

Resolution control needs an original affected incarnation set, a timestamp,
initial custody distinct from ownership, and permanent expiration of an ended
source duration. Optional self untap must retain the physical step without
priority, repeated trigger discovery, or sequentially changing its untap set.

The feature's typed handlers were registered, but its two operations were
missing from the explicit semantic validity inventory. The CI registry
invariant caught that omission. Adding them invokes the existing universal
operation review gate even though runtime registration already recognizes
their strict handlers.

## Decision

Review `gain_control` and `gain_control_set` as exactly two universal semantic
operations. Strict immutable intents carry the resolving controller, original
public references or closed public query, duration and original source. Direct
source guards require retained continuity; set instructions lock membership
once and retain printed untap/control/haste order. Unknown fields, unsupported
queries and durations, unavailable principals, and stale sources fail closed.

`control_effects.py` uses the existing ContinuousEffect journal and layer-two
evaluator. A zero-timestamp origin retains initial custody separately from
owner. Source identity and continuity are captured before activation costs,
at trigger discovery, and afresh for copies. Resolution timestamps distinguish
conditions that begin during resolution from previously ended durations.
Prerequisite expiration uses the existing dependency/timestamp owner and
reevaluates before committing custody; committed ended durations never restart.

Custody-history version two gates this new control path. Historical absent and
version-one modes retain their earlier execution and serialization. Zone moves
synchronize expiration independently of semantic event emission; simultaneous
zone batches defer that synchronization until every member has committed.
Cleanup and departing-player handling reuse their current owners.

Optional self untap is a separate strict component in the existing static
untap participation family, with its own fine capability. An immutable owned
continuation retains the physical clock, plan, public incarnations, available
subjects and held trigger snapshots. Only the active controller can submit
zero or more unique offered references. The canonical physical coordinator
untaps the original selected set simultaneously and advances once to upkeep.

The operation inventory receives an exact ADR-bound architecture review.
No engine method, direct or unowned state-write allowance, runtime Oracle-text
access, identity dispatch, or size-growth exception is introduced. The engine
and engine-local direct state writes decrease relative to the prior baseline.

## Alternatives

- A separate control registry or restoration annotation stack would duplicate
  duration, layer, incarnation and replay authority.
- Requerying a set after acquiring control would lose its original members.
- Removing every apparently expired guard at once can observe an intermediate
  controller that never commits and prematurely end another duration.
- Weakening registry or architecture invariants would conceal the ownership
  omission. Both invariants remain enforced.

## Consequences

Exact command witnesses cover original control spells and activations, costs,
source death, physical cleanup, ownership queries, private projection,
concessions and replay. Scoped owner diagnostics cover copied continuity,
reentry, cyclic guards, historical boundaries and suppressed zone events.
Optional choices preserve reload, empty decline, held triggers and rollback.
Exchange, player/spell control, dynamic or hidden sets, broader durations,
optional additional untaps, selection limits, and phasing execution remain
material residuals. Independently unsupported card siblings retain their
closure blockers. The final generated bundle and exact-head certification
remain required before publication.
