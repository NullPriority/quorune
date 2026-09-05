---
title: "ADR 0088: typed query-count characteristics and effect amounts"
status: "ADR"
authoritative_source: "query-count characteristic compiler and layer evaluator"
verified: "2026-08-28"
audience: "rules, compiler, continuous-effect, replay, and architecture maintainers"
maintenance: "hand-maintained"
adr_id: "0088"
decision_status: "accepted"
date: "2026-08-28"
---

# ADR 0088: typed query-count characteristics and effect amounts

## Context

Static self modifiers commonly count artifacts, creatures, lands, cards in a
public graveyard, attachments, source counters, or raw hand size. The legacy
owner represented only three enum cases, evaluated graveyards by physical
owner instead of current controller, and inspected copyable rather than current
type-changing characteristics. Expanding that enum would preserve both defects;
evaluating complete characteristics recursively could instead make a count
depend on the modifier it is calculating.

## Decision

Compile one closed `CharacteristicQuantitySpec` and one
`QueryCharacteristicModifierSpec`. The quantity selects a controller,
opponent, or global public zone through `ObjectQuerySpec`, the source's current
attachments or counters, or the controller's raw hand size. The modifier is
either a fixed power/toughness coefficient per matching object or a fixed
minimum gate for fixed power/toughness, supported keywords, or both. The
minimum-gate grammar accepts source-self prefix and suffix forms over closed
public battlefield and graveyard queries.

Before materializing the modifier, evaluate source and counted-object
characteristics only through layer 5: copy, control, text, type, and color.
The resulting integer becomes ordinary layer-6 keyword operations and layer-7c
power/toughness operations. This boundary admits current Changeling, Devoid,
and represented type/color changes without consulting later-layer abilities or
power/toughness and therefore cannot recurse into its own result. Controller-
relative graveyards use the source's current controller; opponent scopes use
every other in-game seat. Hand quantities read only zone cardinality.

Historical `DynamicPowerToughnessSpec` records remain readable, but current
compilation emits the query descriptor.

The same `CharacteristicQuantitySpec` now also owns one closed resolution-time
effect amount. A transport-safe scalar placeholder carries a fixed signed
coefficient and a controller or global battlefield, graveyard, or raw-hand
query. The semantic-runtime amount resolver and semantic-value owner resolve it
using the stack object's locked
controller and the same layer-5 evaluator immediately before the instruction
is lowered. The resulting ordinary integer enters the existing life, damage,
draw, or fixed-token owner, including their replacement and target paths. No
new mutation operation or effect dispatcher is introduced.

## Alternatives

- Add more legacy count enums. Rejected because each term would duplicate zone,
  controller, and characteristic logic.
- Query complete effective characteristics. Rejected because later layers can
  depend on the count currently being calculated.
- Use printed or copyable types. Rejected because represented layer-4 and
  layer-5 changes are authoritative inputs to these quantities.
- Resolve each effect family independently. Rejected because that would create
  four competing quantity evaluators with different controller and layer
  semantics.
- Add a family-specific ability-removal check. Rejected because static ability
  addition and removal must share one future layer-6 applicability query.

## Consequences

The represented static family recomputes after control, zone, phasing,
attachment, counter, hand-size, and layer-5 characteristic changes, composes in
ordinary layer order, copies as typed executable data, preserves hidden
identity, and replays exactly. Resolution-time effect amounts instead use the
stack controller frozen by casting, activation, copying, or trigger placement;
they observe the current layer-5 query when that instruction executes.

Delirium distinct-type counts, Domain or color cardinality, chosen, named,
modified, shared-type, top-library, secret-identity, ability-presence, dynamic-
coefficient, comparison, and attached-subject static quantities remain
residual. Resolution-time amounts additionally exclude source counters,
attachments, opponent hidden zones, source exclusion, linked results,
`this way`, optional, modal, conditional, compound, quoted, granted, dynamic-
target, and open-arithmetic forms. Inverted existence wording is also excluded
from the static minimum-gate grammar. Cards with an ability-removal sibling
remain withheld by complete-card admission until the shared layer-6
ability-presence owner exists.

## Removal condition

Retire these descriptors only if a successor preserves the same closed grammar,
cycle-safe layer-5 boundary, current-controller and multiplayer relations,
ordinary layer ordering, locked stack-controller effect amounts, copied-source
identity, existing replacement and target ownership, privacy, replay, and
explicit ability-removal exclusion without runtime prose or card identity
dispatch.
