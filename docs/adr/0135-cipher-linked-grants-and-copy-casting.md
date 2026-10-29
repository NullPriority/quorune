---
title: "ADR 0135: Cipher linked grants and copy casting"
status: "ADR"
authoritative_source: "Cipher model, semantic choices, continuous grants and one-shot casting"
verified: "2026-10-28"
audience: "rules, persistence and compiler contributors"
maintenance: "hand-maintained"
adr_id: "0135"
decision_status: "accepted"
date: "2026-10-28"
---

# ADR 0135: Cipher linked grants and copy casting

## Context

Cipher joins a resolving physical spell, an exiled card and an ability granted
to another permanent. The grant follows that permanent through controller and
type changes. A pending damage trigger locks its controller and encoded-card
reference independently of later permanent departure. Existing records require
stable semantic registries and deterministic projected choices.

## Decision

An immutable `CipherEncoding` retains the card and encoded object's original
incarnations. A registered optional semantic choice emits an `EncodeCipherIntent`;
the rules owner commits the canonical stack-to-exile transition and link. Link
reconciliation occurs after actual zone commits. Encoding follows the complete
ordinary spell body and skips noncard copies.

The exiled card supplies a registered layer-six grant. Version two of the
existing granted-trigger fragment adds the typed encoded-card reference;
version one retains its exact fields. Trigger discovery derives a distinct
ordinary program from the trusted declaration without inserting it into the
semantic registry. The stack item retains the link and damage-time controller.

The existing one-shot exile-cast selection owner creates and casts the copy
through normal target, cost and casting authority. A declined temporary copy
receives no later play permission. Encoding choices sort current creature
references canonically. Unknown companion bodies remain residual.

Continuous registry assembly has a dedicated module. The existing public
factory delegates to it; all handler registrations and their order are
preserved by an AST comparison. This is one registry and one layer engine.

## Alternatives

A permanent-local delayed trigger would put the ability on the wrong source
and could survive loss of the exile link. A new copy-casting engine would
duplicate target, cost, prohibition and continuation authority. Inserting a
new semantic program during damage discovery changes the record registry
fingerprint and breaks pre-action replay. The chosen design keeps the link
typed, the grant in the ordinary layer engine, and trigger derivation transient.

## Consequences

New compiled descriptors and version-two fragments opt into Cipher. Historical
programs without them retain their prior meaning; no GameState mode or global
event reinterpretation is introduced. Pending encoding and casting continuations
remain explicit record data and must validate their actor and incarnations.
The source symbol, ordinary body, physical card, grant and cast-copy boundaries
each retain their own capability checks.

Card recognition, strict closure and successful native composition remain
separate claims. Whole-card carriers are counted only after their ordinary
results and Cipher lifecycle have executable evidence.
