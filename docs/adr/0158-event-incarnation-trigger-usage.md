---
title: "ADR 0158: event-incarnation trigger usage"
status: "ADR"
authoritative_source: "Trigger-limit codec, canonical usage owner and pending trigger batch"
verified: "2026-11-02"
audience: "rules, compiler, replay and multiplayer contributors"
maintenance: "hand-maintained"
adr_id: "0158"
decision_status: "accepted"
date: "2026-11-02"
---

# ADR 0158: event-incarnation trigger usage

## Context

A once-per-turn trigger consumes its allowance when it triggers. Countering the
ability or removing it from the stack does not renew that allowance. Control
changes preserve the same object's use; departure and reentry create a new
object. CR 603.3a and 603.10 require the appropriate event-time controller and
look-back source when a qualifying departure occurs.

The original trigger-limit codec admits supported nondeparture battlefield
events. Its discovery gate requires the captured source to remain the current
incarnation. Removing that gate would skip unused departed sources or read a
fresh graveyard object's cleared annotations as unused historical quota.

## Decision

Preserve schema one's exact fields, admission boundary and execution. Schema
two explicitly carries `source_scope: event_incarnation` for represented single
battlefield departure/death subscriptions. It requires the independent
`trigger.usage.departure_incarnation` capability in addition to ordinary usage
authority. Compiler declaration and runtime inference both derive that promise
from the descriptor. Unknown scopes, unsupported subscriptions and stripped
capability annotations fail closed.

A pure plan reads the captured source's use and any current same-incarnation
use. A surviving source commits through the existing annotation usage owner.
A departed source does not write to the fresh graveyard or battlefield object
and does not mutate captured LKI. Instead, its accepted trigger carries a
closed `event_source_trigger_usage` fact in the existing pending batch. That
fact pins source object and logical identity, semantic key, ability identity
and turn sequence. Subsequent occurrences consult the accepted claims before
allocating another trigger.

Pending claims distinguish separate sources, abilities, incarnations and turns.
Malformed claims, future turns and missing facts on matching departure triggers
reject through the existing command transaction. Existing event matching,
source ability presence, APNAP placement, replacement staging and principal
authority remain unchanged. Additional-trigger multiplication remains bounded
by the once-per-turn allowance; stack copies retain ordinary copy behavior.

No second usage journal, component registry, APNAP coordinator or global state
field is added. Ordinary zone reset still refreshes a returning object. The
new context exists only for explicit schema-two programs. Earlier programs and
records keep their prior serialization and declared runtime provenance.

## Alternatives

Reading only the current card loses the old incarnation's usage and controller.
Writing old usage onto that card can suppress its new incarnation. Mutating
captured LKI changes the meaning of a shared snapshot. A controller journal
resets incorrectly on control changes. Counting resolutions refunds countered
triggers. Broadly removing the departure denylist admits scopes without a
represented event-time contract.

## Consequences

Used and unused departing sources have distinct outcomes, including simultaneous
departure with other qualifying objects. Independent copies keep independent
allowances. Actual control change, blink, countering, atomic rollback, pending
load and command replay remain observable through existing owners. Unsupported
multi-event, stack-countered and action-only limits remain explicit boundaries.
This extends [ADR 0109](0109-typed-trigger-usage-limits.md) without changing its
historical schema-one contract.
