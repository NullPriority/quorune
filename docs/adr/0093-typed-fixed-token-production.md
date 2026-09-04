---
title: "ADR 0093: typed fixed token production"
status: "ADR"
authoritative_source: "fixed token compiler grammar, capability shapes, standard token profiles, and replacement-aware token transaction"
verified: "2026-09-04"
audience: "rules, compiler, token, trigger, mana, replay, and architecture maintainers"
maintenance: "hand-maintained"
adr_id: "0093"
decision_status: "accepted"
date: "2026-09-04"
---

# ADR 0093: typed fixed token production

## Context

Fixed token instructions appear as spell effects, triggered and activated
effects, keyword actions, keyword abilities, target-copy effects, and delayed
effects. Their definitions may contain a proper name, Legendary supertype,
represented keyword, characteristic-defining ability, declaration restriction,
or predefined artifact ability. Treating those forms as unrelated compilers
would duplicate replacement ordering and make token behavior depend on display
text. Treating Afterlife as a creature-dies trigger would also miss a
noncreature permanent with Afterlife, while storing only a stable target
reference would let a copy effect follow a new incarnation.

## Decision

Extend the closed fixed-token grammar while keeping `token_creation.py` as the
only mutation owner. Every admitted definition is a positive fixed quantity
with canonical characteristics. Proper names and Legendary status are data;
Changeling and the exact can't-block or can't-be-blocked sentences are typed
ability fragments. Powerstone, Junk, and Vibranium select closed standard
profiles whose executable behavior never comes from their display text.

Canonical Investigate lowers to the canonical Clue definition. Printed fixed
Afterlife lowers to `permanent.graveyard.self`, preserving previous-controller
and current-ability last-known information, then enters the ordinary APNAP
batch. Repeated Afterlife instances receive distinct generated trigger
identities. One target-copy form uses the existing target schema and
resolution-time incarnation revalidation before the token owner reads current
copiable values. One source-independent next-end-step form stores exactly one
closed token body in the existing delayed-trigger owner.

Restricted fixed-output mana remains in the existing mana descriptor and
payment path. Its new capability admits only one colorless unit with the
Powerstone nonartifact-spell prohibition, or one selectable any-color unit
restricted to artifact spells or creature spells. The spell payment context
contains both artifact and creature type facts so offers and accepted payments
use the same classification.

## Alternatives

- Add card-name handlers for each predefined token. Rejected because token
  identity is compiled data, not runtime authority.
- Interpret a token's quoted display text during activation or combat.
  Rejected because display prose is not executable state.
- Model Afterlife as `creature.dies`. Rejected because its Oracle event is a
  permanent moving from the battlefield to a graveyard.
- Reuse only the target's stable reference for copy tokens. Rejected because a
  leave-and-reentered target is a new object.
- Create a second delayed-trigger or token transaction. Rejected because the
  existing owners already preserve APNAP, replacement ordering, privacy, and
  replay.

## Consequences

The bounded family shares one compiler and capability-shape boundary across
spell, triggered, activated, Investigate, Afterlife, targeted-copy, and delayed
contexts. Token creation continues to use one immutable replacement event and
one timestamped commit. Standard profiles, characteristic fragments, target
snapshots, restricted mana, pending triggers, and their choices remain
serialized and replayable. No runtime Oracle parser, card-identity dispatch,
parallel trigger engine, or parallel token owner is introduced.

Variable or per-object quantities, Incubate, Roles, attached or attacking
tokens, source-LKI and modified copies, arbitrary quoted abilities, unsupported
keywords or predefined definitions, copied or granted Afterlife, linked
results, other delayed times, and other mana restrictions remain material
residuals.

## Removal condition

Retire this boundary only if a successor preserves the same closed grammar,
typed token characteristics, normalized Afterlife event and LKI, distinct
ability-instance identity, target-incarnation revalidation, delayed-trigger
ownership, restricted-mana payment context, replacement ordering, privacy,
rollback, and exact replay without runtime prose or card-specific behavior.
