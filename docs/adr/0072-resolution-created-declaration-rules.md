---
title: "ADR 0072: resolution-created declaration rules"
status: "ADR"
authoritative_source: "resolution declaration-rule model, journal owner, and combat query"
verified: "2026-08-26"
audience: "rules, compiler, combat, replay, and architecture contributors"
maintenance: "hand-maintained"
adr_id: "0072"
decision_status: "accepted"
date: "2026-08-16"
---

# ADR 0072: resolution-created declaration rules

## Context

The compiler represents four closed effects that make one creature unable to
attack, block, both, or be blocked until end of turn. The original producer
covered activated abilities targeting a creature. The same result also appears
as a direct spell or normalized triggered body, can refer to the source itself,
and can target one pinned creature subtype. Its initial implementation stored
the resolved result as a layer-6 added ability fragment. That made
`remove_all_abilities` erase a rule already created by a resolving ability,
even though removing the affected object's abilities cannot end that
independent effect.

The duration journal, resolving-source provenance, target-incarnation lock,
cleanup, rollback, and replay boundaries remain correct. The defect is the
classification and consumption of the journaled result.

## Decision

Represent an object-anchored resolution result with
`ResolutionDeclarationRuleEffect`. It remains in the existing continuous-
effect duration journal, but it has no characteristic layer or characteristic
operation. Its closed payload is one typed `DeclarationRestrictionTemplate`,
one resolving source identity, one or more locked physical and logical object
identities, and the represented until-end-of-turn duration.

One shared compiler template admits the exact whole-clause forms from direct
spells, normalized typed event triggers, and activated abilities. A target
subject is revalidated through the ordinary target schema; a source-self or
exact printed source-name subject locks the source's current battlefield
incarnation. Creature-subtype targets use the pinned shared subtype vocabulary.
The runtime operation and journal payload are unchanged across those contexts.

Combat consumes one `current_declaration_restrictions` query. That query
combines restrictions derived from each battlefield object's current effective
ability fragments with resolution-created rules locked to that object's
current incarnation. Ability addition and removal therefore continue to govern
static components through the shared effective-ability boundary, while an
already resolved rule survives `remove_all_abilities`.

Resolving-source identity is provenance, not a source-presence dependency.
Source departure does not end the represented effect. Target departure changes
logical identity, so the returned object is not affected. Cleanup expires both
characteristic entries and declaration-rule entries through the same journal
owner. Projection exposes only public declaration domains and restrictions;
the journal's physical, logical, source, and effect identities remain
authoritative-only data.

## Alternatives

- Keep the layer-6 grant and special-case ability removal. Rejected because it
  would still classify a game rule as a characteristic and would give ability
  removal incorrect authority.
- Add a temporary marker to cards or a second combat validator. Rejected
  because either path would bypass the journal, incarnation lock, unified
  declaration query, or transaction boundary.
- Make resolution-created rules depend on the source remaining present.
  Rejected because the represented effects state no such duration.
- Generalize two-object source-relative, filtered, conditional, dynamic, or
  non-turn durations in this correction. Rejected because those forms require
  additional binding, declaration-solver, or characteristic-dependency
  semantics.

## Consequences

- Direct spells, normalized event triggers, target activations, source-self and
  exact named-source activations, and pinned subtype targets share one
  CardProgram operation and runtime owner.
- Static restrictions disappear when their current effective ability is
  removed, while resolved restrictions do not.
- Target revalidation, incarnation isolation, cleanup, exact replay, rollback,
  and four-player privacy remain enforced by typed owners.
- The declaration-rule capability is distinct from resolution-created
  characteristic capabilities and does not claim a CR 613 layer.
- Dynamic characteristic-count predicates remain outside trust until they use
  a cycle-safe characteristic boundary or explicitly exclude affected
  type-changing interactions.
- Multiple-block capacity, defender suppression, all-able blocker requirements,
  filtered evasion, and two-object source-relative restrictions remain outside
  this object-anchored prohibition model.

## Removal condition

Retain this boundary while resolution-created declaration rules remain
duration-bound journal entries rather than object characteristics. A future
general declaration-rule system may supersede it only if it preserves the
typed payload, source provenance, target incarnation, shared static-plus-
resolved query, rollback, privacy, cleanup, and replay guarantees.
