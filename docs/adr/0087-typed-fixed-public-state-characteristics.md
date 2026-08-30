---
title: "ADR 0087: typed fixed public-state characteristics"
status: "ADR"
authoritative_source: "fixed public-state characteristic compiler and continuous-effect runtime"
verified: "2026-08-30"
audience: "rules, compiler, continuous-effect, replay, and architecture maintainers"
maintenance: "hand-maintained"
adr_id: "0087"
decision_status: "accepted"
date: "2026-08-28"
---

# ADR 0087: typed fixed public-state characteristics

## Context

Many static abilities apply fixed power, toughness, or supported keywords only
while one public game-state condition is true. The unconditional source,
attached-object, and fixed-query characteristic owners already represent the
effect bodies, but their applicability does not represent turn, raw card-count,
life-total, battlefield-entry-turn, or named-counter gates. Inferring those
conditions from Oracle text during characteristic evaluation would make
authoritative behavior, projection, and replay depend on runtime prose.

## Decision

Lower one closed condition, one existing closed target, and one existing fixed
characteristic modifier into a versioned semantic descriptor. The represented
conditions are the source controller's turn or another player's turn; fixed
bounds over the source controller's graveyard card count, public hand count, or
life total; a fixed upper bound over any opponent's life total; source entry
during the current turn; and a fixed minimum of one canonical named counter on
the source.

The additive v2 condition schema also admits a source or attached-object
`ObjectQuerySpec`, and a fixed minimum over the existing typed public quantity.
Source state is limited to attacking, blocking, tapped, untapped, enchanted,
equipped, modified, or monstrous. Attached-object conditions use effective
type, subtype, supertype, and color through layer 5. Fixed battlefield counts
reuse `CharacteristicQuantitySpec` and the existing layer-5 count resolver, so
type-changing effects participate without recursing into the layer-6 or
layer-7c result being gated.

The CardProgram runtime derives a typed public snapshot from authoritative
state for each active battlefield source. Runtime query evaluation remains in
the domain layer; the rules-layer snapshot carries only its typed boolean and
quantity results. The semantic component evaluates the condition once and
emits both layer-6 additions and layer-7c modifiers over the same source,
attached-object, or `ObjectQuerySpec` applicability. The same
permanent-state predicate supplies attacking, blocking, tapped, untapped,
enchanted, equipped, and modified affected sets to the unconditional fixed-
query owners. Exact logical object identity prevents an old source or
attachment incarnation from selecting behavior. Existing keyword-consumer
capabilities and continuous-effect operations remain the mutation and rules
owners.

## Alternatives

- Add condition fields independently to every characteristic component.
  Rejected because paired layers could diverge and each family would grow a
  separate applicability vocabulary.
- Reparse Oracle text during characteristic evaluation. Rejected because
  trusted runtime behavior must consume typed CardProgram data only.
- Evaluate characteristic-dependent object counts from final characteristics.
  Rejected because later-layer abilities and power/toughness can recurse. The
  accepted count boundary stops after layer 5.
- Expose hand contents so the runtime can count them. Rejected because only the
  authoritative count is relevant and hidden identities must remain private.

## Consequences

The family continuously recomputes when turns, zones, hand counts, life totals,
control, counters, combat participation, tap state, monstrous designation,
effective layer-5 characteristics, attachment identity, or source presence
change. A creature is modified when it has a counter, is equipped, or is
enchanted by an Aura controlled by that creature's controller. Projection
exposes effective public characteristics without exposing hidden card identity,
and replay pins the typed condition and source identity.

Delirium, distinct-type and dynamic quantities, dynamic amounts, unmarked exact
counts, per-opponent thresholds above existence, top-library predicates, chosen
or secret contents, historical-action, compound state-and-counter, and exact-
attachment-count predicates, quoted abilities, type or color changes, ability
removal, Class levels, Protection, Ward, and unsupported keywords remain source-
spanned residuals. This owner does not introduce an ability-presence-specific
layer-6 check; future ability addition and removal must continue through one
shared applicability boundary.

## Removal condition

Retire this descriptor only if a successor preserves the same closed grammar,
authoritative public-state snapshot, identical cross-layer applicability,
logical-object identity, capability closure, source departure and control
semantics, multiplayer privacy, and exact replay without runtime prose or card
identity dispatch.
