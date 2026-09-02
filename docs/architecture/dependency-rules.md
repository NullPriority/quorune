---
title: "Dependency and mutation rules"
status: "current"
authoritative_source: "platform/architecture-policy.json and architecture validator"
verified: "2026-08-07"
audience: "all code contributors"
maintenance: "hand-maintained"
---

# Dependency and mutation rules

The executable policy in `platform/architecture-policy.json` is authoritative.
This document explains its intent.

```mermaid
flowchart LR
    Browser["browser"] --> Server["transport/adapters"]
    Server --> Application["service/session"]
    Application --> Rules["rules/domain"]
    Rules --> Model["typed model/value objects"]
    Compiler["compiler/semantic data"] --> Rules
    Persistence["persistence adapters"] --> Application
```

Dependencies point toward the domain. Protected rules/domain modules may not
import server frameworks, WebSockets, persistence adapters, AI providers, or
application/session orchestration. Compiler and metadata code may describe
rules programs but may not acquire runtime mutation authority.

`quorune/card_programs/` owns the deterministic CardProgram V2 model,
generated/reviewed adapters, source/trust validation, and inspection commands.
It may depend on compiler output, semantic value objects, the card database,
and capability metadata. It must not depend on transport, server, pilot,
session, or persistence orchestration, and it never mutates `GameState`.

`quorune/semantic_runtime/` owns typed node handlers, their immutable
query context, typed intents, deterministic registry, and canonical intent
executor. Its scoped architecture policy additionally forbids imports of the
engine, state model, record, or projection modules. The engine may call inward
to this pure boundary; handlers cannot call outward to authoritative state.
The executor may route a typed intent to a classified rules-layer mutation
port. `quorune/tap_state.py` is such a port. The replacement event
boundary also routes represented counter events to
`quorune/counter_placement.py`. Represented damage events route to
`quorune/damage.py` for proposal preparation, replacement/prevention,
atomic result commit, and final-event dispatch. Direct destruction,
permanent-exile, battlefield return-to-owner-hand, and own-graveyard card
return intents route to
`quorune/destruction.py`, `quorune/permanent_exile.py`, and
`quorune/return_to_hand.py`. Fixed public-origin direct and affected-set intents
route to `quorune/public_zone_moves.py`; CR 903.9 owner choices are modeled by
`quorune/commander_zones.py` and the public nontarget choice owner. Fixed
entry-return requirements reuse that choice boundary with an owner-hand
destination and still commit only through the simultaneous zone-transition
owner. Exile,
return, and public-set movement share the closed
`rules/single_object_zone_transition.py` typed-origin preparation, stale-plan
validation, and the canonical simultaneous zone-transition substrate while
retaining distinct results and journals. Destruction
remains a separate disposition family. These transaction owners delegate
authoritative counter or zone writes to existing canonical owners. Direct stack-counter intents route to
`quorune/stack_counter.py`, which owns counterability, stack removal,
replacement-aware physical spell movement, normalized counter-event dispatch,
telemetry, and public journaling behind a narrow host protocol. These ports depend on narrow
structural host protocols rather than the engine class and are authorized by
[ADR 0009](../adr/0009-typed-tap-state-mutation-owner.md) and
[ADR 0011](../adr/0011-counter-placement-event-and-mutation-owner.md), plus
[ADR 0012](../adr/0012-damage-transaction-and-static-prevention.md),
[ADR 0027](../adr/0027-typed-permanent-destruction.md),
[ADR 0028](../adr/0028-typed-return-to-owner-hand.md),
[ADR 0029](../adr/0029-typed-permanent-exile.md),
[ADR 0030](../adr/0030-typed-stack-counter.md), and
[ADR 0053](../adr/0053-typed-own-graveyard-return-to-hand.md).

Represented zone-destination replacements use an additional narrow split.
`semantic_runtime/zone_replacement_model.py` owns immutable affected-object,
source-effect, and prepared-move values. `zone_replacements.py` owns read-only
descriptor discovery and APNAP preparation. Single and simultaneous moves
capture that model once before mutation. Only `CommanderEngine.move_card`
commits zone membership, so replacement discovery cannot become a competing
state owner.

Every production Python module has one generated exact classification covering
layer, owner, allowed dependency layers, GameState access, specificity,
visibility, and replay participation. A new unclassified module fails CI.
Direct writes are ratcheted by stable file/symbol/mutation/state-path identity,
not line number or aggregate count. Architecture exceptions bind to one exact
ADR and allowance fingerprint.

`CommanderEngine` remains a measured legacy mutation boundary while it is
decomposed. New engine methods, direct `GameState` write sites, fixed card
identity flows that select legality, mutation, implementations, or outcomes,
card-specific operations/helpers, oversized modules/functions, or unreviewed
dependency exceptions fail the architecture gate. Card names, face identity,
collector numbers, set codes, and Oracle IDs may remain data for display,
compiler binding, replay provenance, and typed rules values. Reviewed historical
compatibility and explicit override modules remain exact classified boundaries;
they are not generic-runtime exemptions. Existing debt is ratcheted rather than
endorsed. Engine net logical growth defaults to zero, and existing oversized
modules/functions may not grow. See
[ADR 0064](../adr/0064-context-aware-card-identity-guard.md).

Any new subsystem documents ownership and dependencies. Changing mutation
ownership or adding a cross-layer dependency requires an ADR. Reviewed legacy
exceptions require an ADR and removal plan. The identity-flow guard has no
growth allowance or card-database-derived exception surface.

The separate raw Oracle-ID-literal ratchet remains active. It protects literal
identity leakage and reviewed historical debt independently of whether a literal
currently reaches a behavior-selection sink; it does not replace or weaken the
identity-flow invariant.
