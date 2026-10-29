---
title: "ADR 0124: resolved declaration requirements"
status: "ADR"
authoritative_source: "declaration fragments, duration journal and combat requirement projection"
verified: "2026-10-26"
audience: "rules, compiler, combat and replay contributors"
maintenance: "hand-maintained"
adr_id: "0124"
decision_status: "accepted"
date: "2026-10-26"
---

# ADR 0124: resolved declaration requirements

## Context

CR 509.1c requires the maximum compatible blocking requirements without
violating restrictions or forcing optional payments. An attached static rule
belongs to its Aura or Equipment. A requirement created by resolution is an
independent game rule even after the affected creature loses its abilities.
The prohibition-only scope of [ADR 0072](0072-resolution-created-declaration-rules.md)
therefore needs a typed requirement extension.

## Decision

Keep static attached requirements on their current giver through a version-two
declaration requirement fragment. The fragment binds either the current
attachment or a conditional source. Closed attached-characteristic and
controller-graveyard conditions reuse the existing public snapshot owner.
Mixed characteristic clauses retain a separate ordinary continuous modifier.
Ability removal on the giver ends its rule; ability removal on its recipient
does not.

Extend `ResolutionDeclarationRuleEffect` with one exact version-three codec
whose `requirement` field replaces `restriction`. Admit only an unconditional
self-scoped all-able-blocker requirement, one locked physical and logical
battlefield incarnation, and the existing turn duration. Preserve the legacy
restriction codec and version-two live-query codec exactly. No live group
requirement is admitted.

Use the existing declaration effect operation, duration journal, canonical
commit owner and cleanup. The direct target production uses ordinary target
revalidation. A source-self instruction binds the sealed source incarnation;
a departed and returned source cannot become its recipient. Source identity
in a committed journal entry is provenance, not an ongoing presence condition.

Project static and resolved requirements into the existing declaration solver.
Keep resolved requirements out of the restriction query and characteristic
layers. Offers and accepted declarations therefore use the same legal domains,
cost exemptions, restrictions and maximum-satisfaction result.

## Alternatives

- Granting a removable creature ability gives ability removal incorrect
  authority over both attached and resolved rules.
- A separate combat marker or validator duplicates duration, incarnation and
  declaration authority.
- General live groups, filtered blockers and other durations require their own
  explicit typed binding and remain outside this production.

## Consequences

Native witnesses distinguish recipient ability loss, giver departure and
reattachment, current attached color, the giver controller's graveyard,
impossible Menace requirements, cleanup and source blink before resolution.
Malformed shapes and missing capability minima fail closed. Existing rollback,
principal projection and exact replay preserve the same authority boundary.

## Removal condition

Retain this extension while the duration journal owns resolved declaration
rules. Any replacement must preserve the historical codecs, independent rules,
incarnation isolation, cleanup and shared declaration solver.
