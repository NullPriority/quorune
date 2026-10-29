---
title: "ADR 0137: Linked optional casting declarations"
status: "ADR"
authoritative_source: "declared casting-cost model, zone-cost union and existing condition/entry owners"
verified: "2026-10-28"
audience: "rules, compiler and persistence contributors"
maintenance: "hand-maintained"
adr_id: "0137"
decision_status: "accepted"
date: "2026-10-28"
---

# ADR 0137: Linked optional casting declarations

## Context

Bargain is declared while casting and can qualify a linked spell or entry
result. Its price admits an artifact, enchantment or token. A conjunction of
those predicates loses legal choices. Inferring the declaration from a later
graveyard state loses tokens, redirected sacrifices and object identity.
Spell copies retain casting choices, while ordinary permanent copies do not
inherit a different object's casting history.

## Decision

`DeclaredCastCostFacts` is one immutable versioned container of optional-cost
choices, keyed by source-local instance and a closed cost-kind vocabulary.
Its initial kind is Bargain. This container records the declaration of intent
under CR 702.166b; actual costs still commit through ordinary casting authority,
and failed commands roll back both. Existing Kicker and other fields are not
reinterpreted or migrated.

Schema five of the existing zone-change additional cost adds a closed union
of current controlled-permanent type/token predicates. Selection, prospective
source-placement reads, replacement and payment retain their existing owners.
The union has its own structural capability and cannot appear in an older
schema's field set.

Linked fixed results use version twelve of the existing public-condition
instruction. The compiler validates the specific declaration and each ordinary
result branch. Same-target replacement and appended-keyword forms retain their
complete target contract. Unrepresented conditional announcement targets remain
residual instead of becoming unconditional targets.

The zone-state owner retains casting declarations for the original resulting
permanent. Original entry occurrences freeze those facts and publish chosen
instance identifiers to ordinary trigger discovery. Older occurrences omit the
new data. A queued trigger retains its original entry context after departure
or unpaid reentry. Ordinary spell-copy context and retained copy templates carry
the declaration; ordinary permanent copying does not copy that history.

## Alternatives

Independent per-card flags would scatter lifetime and linking rules. Reusing a
Kicker marker would give one additional cost another cost's meaning. A runtime
Oracle query would bypass source-spanned compilation. A new sacrifice or copy
engine would duplicate legality, replacement and replay authority. The selected
container and predicate union extend existing owners without another registry
or mutable global flag.

## Consequences

The declaration, price predicate and each linked result remain independently
capability-bound. Invalid or missing linked instances fail closed before a
result executes. Old event payloads and cost codecs retain their existing
representation. New metadata adds no game-state mode or checkpoint field.
Whole-card support requires native evidence for both casting and ordinary
results; unknown bodies, discounts, conditional targets and unrepresented
multiple/granted instances remain explicit unfinished support.
