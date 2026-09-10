---
title: "ADR 0096: typed fixed source characteristic effects"
status: "ADR"
authoritative_source: "fixed source-characteristic compiler, capability shape, semantic operation, and continuous-effect journal"
verified: "2026-09-10"
audience: "rules, compiler, continuous-effect, replay, and architecture maintainers"
maintenance: "hand-maintained"
adr_id: "0096"
decision_status: "accepted"
date: "2026-09-10"
---

# ADR 0096: typed fixed source characteristic effects

## Context

Artifact animation and compound self-characteristic instructions create one
continuous effect whose parts apply in several layers. Type, subtype, color,
supported abilities, base power/toughness, and fixed power/toughness modifiers
must share one timestamp even though the existing resolution helper commits
one layer at a time. Compiling the parts as unrelated semantic effects would
give them different timestamps and would lose the rules identity of the
single resolving instruction.

The existing target and controlled-set compilers already own fixed temporary
statistics and supported keywords. The canonical continuous evaluator already
orders type changes before dynamic characteristic-defining counts and base
power/toughness settings. A second characteristic evaluator or direct card
mutation would duplicate those authorities.

## Decision

Register `apply_source_characteristics_until_end_of_turn` as one closed
universal semantic operation. Its compiler-owned schema names only the current
`$source.zone_object` incarnation and fixed nullable fields for card types,
creature subtypes, colors, supported keywords, base power/toughness, and fixed
power/toughness modifiers. Runtime validation rejects unknown fields,
noncanonical types, subtypes, colors or keywords, mixed animation and modifier
shapes, dynamic values, and empty effects before any journal mutation.

`ResolutionContinuousComponent` and
`create_resolution_continuous_effect_components` validate every component,
allocate one timestamp, and atomically append the layer-4, layer-5, layer-6,
layer-7b, and layer-7c entries to the existing continuous-effect journal. The
components retain separate effect IDs for deterministic serialization but one
shared timestamp and one locked physical/logical source identity.

The fixed source compiler accepts only literal artifact animation, fixed
power/toughness plus supported keyword composition, two supported keyword
grants, and colorless or all-colors source changes. The targeted characteristic
compiler now consumes the repository's shared supported-keyword vocabulary,
so newly admitted Shadow, Fear, Infect, and other represented keywords use
their existing gameplay capabilities rather than a family-local allowlist.

This ADR reviews the new operation and the source-checkpoint size baseline for
`quorune/rules/capabilities.py`,
`quorune/rules/node_capability_shapes.py`,
`typed_resolution_effect_template`, and
`_apply_source_characteristics_until_end_of_turn`. Those surfaces contain
only capability routing, closed shape validation, or canonical dispatch; the
change adds no `CommanderEngine` method and no unowned state write.

## Alternatives

- Emit one ordinary semantic effect per layer. Rejected because independently
  allocated timestamps misrepresent one resolving continuous effect.
- Mutate `CardInstance` annotations directly. Rejected because it bypasses
  canonical layer ordering, logical-object expiry, replay, and shared effective
  characteristics.
- Add artifact-animation or card-name branches. Rejected because the operation
  is a generic typed characteristic result with no Oracle identity authority.
- Admit land animation and open type-changing prose. Rejected because retains-
  type riders, choices, dynamic values, and declaration riders need separate
  grammar and evidence.

## Consequences

Fixed source characteristic effects compose with static ability addition and
removal through the same layer-6 applicability query. Layer-4 animation feeds
the existing cycle-safe layer-7a dynamic count before the animation's layer-7b
base statistics. Equipment that becomes a creature is detached by the existing
state-based action owner. Control change preserves the locked effect on the
same incarnation; source departure and reentry do not retarget it; cleanup
expires every component; public projection exposes only effective public
characteristics; and save/load plus replay preserve the shared timestamp.

Land animation, retains-type riders, dynamic or chosen characteristics,
copies, text or control changes, Protection, landwalk, Banding, unsupported
keywords, declaration riders, attachment-relative effects, indefinite
durations, and same-layer dependency interactions remain fail-closed
source-spanned residuals.

## Removal condition

Retire the composite operation only if a successor preserves pre-mutation
closed validation, one shared timestamp across every represented layer,
canonical layer and dependency ordering, shared layer-6 ability applicability,
cycle-safe dynamic counts, locked source incarnation, Equipment detachment,
control and zone-change boundaries, cleanup, privacy, rollback, save/load, and
exact replay without runtime Oracle prose, card identity, or direct state
mutation. Split the reviewed routing or handler surfaces before adding another
unrelated characteristic family that would grow their baseline.
