---
title: "ADR 0119: fixed-mana Squad repetitions"
status: "ADR"
authoritative_source: "paid-entry cost expansion, Squad count form, sealed zone facts and canonical token batch"
verified: "2026-10-16"
audience: "rules, compiler, casting, privacy and replay contributors"
maintenance: "hand-maintained"
adr_id: "0119"
decision_status: "accepted"
date: "2026-10-16"
---

# ADR 0119: fixed-mana Squad repetitions

## Context

Squad adds a repeatable optional cost and creates that many source-copy tokens
on entry. These payments are casting decisions distinct from printed X. The
tokens enter simultaneously: Securitron paid twice creates two tokens that
are each observed by all three Squadrons. A small count menu or sequential
token arrivals cannot represent this rule.

## Decision

Extend the paid-entry foundation with a Squad discriminator and positive
integer count. Offspring retains its count-one payload and 1/1 exception.
A single printed positive fixed ordinary-mana Squad instance emits a cost
component and linked entry trigger. Multiple printed instances, nonmana,
compound, variable, hybrid and unsupported face costs remain residual.

Each paid variant publishes an integer `squad_count` through the existing
choice-schema form adapter: minimum one, default one, no arbitrary ceiling.
The ordinary option represents zero payments. The server validates the count
and selected variant, adds the multiplied fee to the base cost, applies total
modifiers and checks current affordability. Counts change neither mana value
nor printed X. Malformed counts and unpaid-option/positive-count mismatches
reject before mutation.

The accepted stack fact owns the count. Copied casting context preserves it,
and the normalized exact entry occurrence seals it for the linked instance.
The trigger's reviewed context reference supplies the count to one canonical
source-copy token batch. The fine capability proves that reference and recipe
independently; it admits no general variable-quantity token grammar.

Current or same-incarnation LKI values, replacement ordering and simultaneous
token entry remain under existing copy and token owners. New tokens retain
Squad without a paid-entry fact, so they do not recursively produce copies.
No universal operation, engine method, runtime Oracle parser, mutable-state
cache or architecture exception is added.

## Alternatives

- Reuse X: rejected because X and Squad are independent decisions.
- Enumerate a fixed payment menu: rejected because every affordable count is
  legal.
- Store a Boolean: rejected because counts have different costs and results.
- Loop over single-token arrivals: rejected because later observers would
  miss earlier members of a simultaneous group.

## Consequences

Actual Sicarian casts exercise zero, one, two and six payments, full prices,
N copies and N+1 draws. Securitron paid twice requires three counters on each
copy and none on the original. Wrong-principal, malformed and unaffordable
submissions, private projection, pending load and exact replay protect the
new path. Existing Offspring behavior remains applicable. A separate copied
casting-decision seam does not implement an unadmitted copy-producing card.

## Removal condition

A successor must preserve independent paid instances, every affordable count,
total-cost ordering, copied casting decisions, exact current/LKI references,
simultaneous token groups, principal projection and historical replay.
