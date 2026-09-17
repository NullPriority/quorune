---
title: "ADR 0100: public cast-cost modifier closure"
status: "ADR"
authoritative_source: "typed cast-cost modifiers, public reduction metrics, total-cost owner, and committed turn history"
verified: "2026-09-17"
audience: "compiler and rules contributors"
maintenance: "hand-maintained"
adr_id: "0100"
decision_status: "accepted"
date: "2026-09-17"
---

# ADR 0100: public cast-cost modifier closure

## Context

The generated frontier grouped thousands of unrelated continuous-layer spans.
A bounded classification isolated a coherent cross-era subset whose only new
authority is the spell's total-cost calculation. Existing owners already
provided selected-face spell characteristics, current layer-6 static-component
applicability, public effective-object queries, source counters, turn history,
mana-vector payment, rollback, and replay, but their closed cast-cost schemas
did not compose those facts.

The complete CR 601.2f and CR 601.2h algorithms remain broader than this
subset. In particular, arbitrary payment-order choices, minimum and direct
total-cost effects, target-relative prices, and open payment-method changes are
not represented.

## Decision

Extend the existing cast-cost modifier owner instead of adding another casting
or cost engine.

- `PublicCastCostModifierV2Spec` represents one fixed signed generic or colored
  mana vector, an optional public multiplier, a closed spell predicate, and the
  existing caster, origin, turn, and ordinal relations.
- `CastReductionMetric` remains the shared public quantity model. Its bounded
  vocabulary includes public object counts and thresholds, current source
  counters, party size, permanent color cardinality, effective-name counts,
  life difference, and sealed discard, sacrifice, life, attack, spell, and
  Commander-cast facts.
- Discard and sacrifice counts consume typed committed zone-transition causes;
  life gain consumes the canonical life commit. No presentation log or Oracle
  prose becomes rules authority.
- Offer construction and command validation reevaluate the same current total-
  cost query. Static-source modifiers continue to require the one shared
  layer-6 component-presence map.
- Dynamic characteristic queries use the current effective characteristic
  boundary. Power, toughness, greatest-value, and distinct type or mana-value
  arithmetic remain outside trust.

## Alternatives

- Add a second dynamic-cost engine. Rejected because the existing offer and
  commit total-cost owner already has the required ordering and rollback path.
- Read Oracle text while pricing a spell. Rejected because current-game prose
  is not rules authority and would split advertised and accepted totals.
- Admit target-relative and arbitrary characteristic arithmetic in the same
  tranche. Rejected because they require additional announcement ordering or a
  cycle boundary not established by this decision.
- Treat discard, sacrifice, and life totals as presentation-log facts. Rejected
  because only typed committed producers can support replay-safe cost queries.

## Consequences

Fixed and public-value increases are applied before reductions, and every
reduction floors only its represented mana component at zero. Existing
additional, alternative, Commander-tax, Convoke, Improvise, Delve, Kicker, and
lifecycle owners continue to assemble or pay their parts of the selected total.

Unsupported target-relative prices, chosen or private facts, caps, floors,
minimum totals, direct total setters, substitutions, copied or text-changed
abilities, and arbitrary payment ordering remain source-spanned residuals. The
change does not claim complete CR 601.2f or CR 601.2h conformance.

The closed object-count parser, metric-shape validator, and multiplier
dispatcher cross the architecture function-size review threshold while keeping
one schema and one evaluation owner. Their exact sizes are reviewed in the
architecture baseline for this decision. They may accept fixes within this
closed family, but an unrelated cost family must first extract smaller
grammar-, validation-, and evaluation-owned helpers instead of growing these
dispatchers.
