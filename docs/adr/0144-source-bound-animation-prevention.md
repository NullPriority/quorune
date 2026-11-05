---
title: "ADR 0144: source-bound animation and prevention"
status: "ADR"
authoritative_source: "fixed source animation, type retention and original-incarnation prevention"
verified: "2026-10-30"
audience: "compiler, characteristic, damage and replay contributors"
maintenance: "hand-maintained"
adr_id: "0144"
decision_status: "accepted"
date: "2026-10-30"
---

# ADR 0144: source-bound animation and prevention

## Context

A fixed source planeswalker animation can retain its previous types and then
create a separate shield preventing damage to that source. CR 205.1b retains
all prior types and subtypes when the instruction says it is still a
planeswalker. Preventing damage to the source does not prevent damage it deals.
Neither instruction may substitute a later incarnation of the same card.

## Decision

Reuse the fixed resolution characteristic descriptor for the animation and
the existing two-clause sequence owner for printed order. Lower pronouns in
the prevention sentence only after a complete source-only animation has
independently identified the same source. Unsupported subjects or extra text
remain residual.

The shield carries `source_subject_version: 1` and the existing
`$source.zone_object` value. Runtime value binding resolves that original
battlefield incarnation, including an unavailable result after departure or
reentry. The prevention owner validates the complete closed versioned
instruction before treating an absent subject as a no-op. A present subject
is sealed by the existing prevention creation transaction and its ordinary
end-of-turn lifetime.

Infer the prevention dependency from the descriptor itself. The extension adds
no operation, callback, registry, identity dispatcher or direct state mutation.
Legacy unversioned and attachment-bound prevention payloads retain their
existing interpretation.

## Alternatives

- Use the stable source reference alone. This can protect a returned object.
- Prevent damage dealt by the source. This reverses the recipient relation.
- Implement a named planeswalker operation. This duplicates existing generic
  characteristic, prevention and sequencing owners.

## Consequences

Native evidence distinguishes retained planeswalker and creature types, base
stats and subtypes, incoming spell and combat damage, outgoing combat damage,
source departure, malformed absent-source requests, and exact replay.
Animation and shields remain independent effects with the same original source
binding and existing cleanup rules.

## Removal condition

Replace this version only with a migration preserving complete validation,
source-incarnation exclusion, incoming/outgoing damage distinctions, layer
ordering, cleanup, rollback and historical replay.
