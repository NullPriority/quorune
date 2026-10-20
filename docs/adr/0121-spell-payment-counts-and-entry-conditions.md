---
title: "ADR 0121: spell payment counts and entry conditions"
status: "ADR"
authoritative_source: "canonical cast payment, stack provenance, dynamic entry amounts and record trust"
verified: "2026-10-20"
audience: "rules, compiler, replay and architecture contributors"
maintenance: "hand-maintained"
adr_id: "0121"
decision_status: "accepted"
date: "2026-10-20"
---

# ADR 0121: spell payment counts and entry conditions

## Context

A condition asking whether at least three white mana was spent cannot use the
number of distinct colors spent or the printed mana cost. CR 400.7d allows an
entering permanent to read the payment of its original spell. CR 609.4b keeps
actual payment types distinct from permissions to spend mana as another type.
Adamant is an ability word; its label supplies no rules behavior.

A spell can acquire an entry ability while entering as a copy. Capturing
payment only when its printed card already contains the entry clause would
miss Clone acquiring the ability after paying its own costs.

## Decision

The casting commit owner captures immutable concrete mana-unit counts for
each original spell in version-one execution. `SpellManaSpentCounts` binds
six exact nonnegative counts to the physical and logical spell incarnation.
The optional stack field is separate from casting decisions and the existing
distinct-color set. Spell copies receive no payment facts, even when they
retain the original spell's choices or entry abilities.

New game states carry `spell_mana_counts_version: 1`. Loading a checkpoint
without that marker selects historical no-count execution; serialization
continues to omit the marker and later casts omit the new stack field. Strict
codecs reject unknown versions, Boolean quantities, missing types, foreign
incarnations and payment facts assigned to a spell copy.

Extend the existing dynamic self-entry amount model with a specified mana
color. The closed compiler production retains the complete original clause,
its fixed positive threshold and counter amount. The existing replacement
handler requires complete-card admission for this variant. Runtime binding
independently requires both the payment-fact and paid-color entry capabilities.
The amount reader consumes the original cast fact; missing provenance fails
closed. Noncast entries and spell copies have zero qualifying payment.

The counter remains an as-enters replacement under the existing affected-object
counter owner. Counter replacements, entry-copy preparation, pending choices,
current ability presence and exact replay retain their existing authorities.
No separate Adamant registry, universal operation, engine method, direct state
write, runtime Oracle parser or mutable gameplay cache is introduced.

Current runtime-trust records also identify the exact core fallback for a
copied permanent spell. The recorder and replay lookup include that copy's
pinned physical spell record and copied-permanent resolution kind. Records
without runtime-trust metadata retain the earlier fallback lookup contract.
This corrects a copy row previously recorded with an unknown Oracle identity
and later compared with the original spell's identity during replay.

## Alternatives

- Reuse the distinct-color set: one white mana and four white mana look equal.
- Read printed pips or the available pool: neither describes actual payment.
- Capture facts only for the printed consumer: misses acquired copy abilities.
- Copy payment with casting decisions: attributes paid mana to an uncast copy.
- Add fields to historical casts: changes their recorded transition hashes.
- Implement an entry trigger: creates the counter at the wrong rules boundary.

## Consequences

Native casts distinguish two, three, excess and wrong-color payment. Clone
uses its own payment after acquiring the entry ability. Noncast reanimation
does not reuse an earlier incarnation's facts. Counter replacement and combat
controls protect the other printed abilities. Pending load, private projection,
exact replay, verified capability withdrawals and fresh producer/reader
mutations provide the scoped behavioral evidence.

## Removal condition

A successor must preserve actual concrete payment, original incarnation,
known-zero versus unavailable facts, copied-decision separation, historical
absence, as-enters ordering and canonical counter replacement. Retire the
execution marker only through an explicit Game Record migration.
