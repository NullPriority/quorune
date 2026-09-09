---
title: "ADR 0094: typed fixed attachment actions"
status: "ADR"
authoritative_source: "fixed attachment compiler grammar, target predicates, token creation, and reciprocal attachment owner"
verified: "2026-09-09"
audience: "rules, compiler, attachment, token, replay, and architecture maintainers"
maintenance: "hand-maintained"
adr_id: "0094"
decision_status: "accepted"
date: "2026-09-09"
---

# ADR 0094: typed fixed attachment actions

## Context

Several Oracle families move the source Equipment or Aura rather than an
independently selected attachment. Fixed restricted Equip variants, source-
entry Equipment triggers, Aura reattachment, Living Weapon, and For Mirrodin!
therefore share source incarnation, target legality, reciprocal attachment,
token replacement, trigger batching, projection, and replay concerns. Treating
each wording as a separate runtime branch would duplicate those authorities.

Living Weapon and For Mirrodin! also create a token and then attach the source
to that original token. The existing semantic effect sequence has no generic
placeholder for the result of a preceding token instruction, so it cannot
express that relationship without either a closed composite instruction or a
new runtime result-binding language.

## Decision

Compile only the reviewed fixed attachment grammar into capability-closed
CardProgram nodes. Restricted Equip reuses the canonical activated-ability
catalog, timing, payment, target offer, current-ability, usage-limit, and stack
owners. Target-selected Equipment and Aura instructions use one source-pinned
`attach` operation and the existing resolution revalidation and reciprocal
attachment transaction.

Register `create_attached_token` as one strict universal composite operation
for ordinary Living Weapon and For Mirrodin! entry triggers. Its handler
validates the entire fixed source, controller, token definition, and quantity
shape before mutation. It delegates token creation and all applicable
replacement choices to the existing token transaction, then delegates the
single source-to-original-token relation to the existing attachment owner. An
independently added replacement token remains unattached. A departed or re-
entered source cannot attach its new incarnation.

The historical Equipment form of `attach` retains its established event keys,
failure result, and invalid-type behavior. The new source/target form adds Aura
legality and no-effect resolution without reinterpreting prior Game Record v3
programs.

## Alternatives

- Add card-name or keyword-specific runtime branches. Rejected because Oracle
  names and display text are not behavior authority.
- Create separate Living Weapon and For Mirrodin! operations. Rejected because
  their only represented difference is the compiled fixed token definition.
- Add a general prior-effect result placeholder. Rejected because this family
  needs only one closed token-to-attachment handoff and does not justify a new
  runtime binding language.
- Attach every token produced by a replacement. Rejected because the
  instruction refers to the original created Germ or Rebel, not independent
  additional tokens.

## Consequences

The selected compiler family shares existing activation, target, token,
attachment, trigger, state-action, privacy, and replay owners. The new
operation performs no direct GameState write, accepts no callback or arbitrary
query, reads no runtime Oracle prose, and dispatches on no card name or Oracle
ID. Malformed composite fields fail before token creation.

Variable or nonordinary Equip costs, broader target restrictions, instant-speed
permissions, Reconfigure, Fortify, multiple targets, player or noncreature
Aura movement, modified keyword token definitions, replacements that multiply
or alter the original token, copied or granted abilities, and independently
unsupported sibling text remain fail-closed residuals. This family adds no
family-specific layer-6 applicability query and performs no dynamic
characteristic count.

## Removal condition

Retire the composite operation only if a successor preserves exact source and
token identity, validation before mutation, applicable token-replacement
ordering, attachment to only the original created token, current legality,
source-incarnation exclusion, APNAP placement, state-action timing, privacy,
rollback, historical Equipment replay, and exact replay without runtime prose
or card-specific dispatch.
