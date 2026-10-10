---
title: "ADR 0112: bounded target characteristics"
status: "ADR"
authoritative_source: "compiler/target_characteristic_sets.py, rules/target_characteristic_sets.py, and continuous_effect_state.py"
verified: "2026-10-10"
audience: "compiler, rules, replay, and architecture contributors"
maintenance: "hand-maintained"
adr_id: "0112"
decision_status: "accepted"
date: "2026-10-10"
---

# ADR 0112: bounded target characteristics

## Context

Homogeneous target instructions can modify each of a fixed bounded set of
creatures. Optional target selection is distinct from optional resolution.
Each target independently retains or loses legality before resolution.

## Decision

Lift one independently closed direct creature characteristic instruction into
its printed target count range. Version six of the existing characteristic
operation carries the selected references, maximum target count, fixed stat
deltas, and represented keywords. Earlier instruction versions retain their
original decoders and execution paths.

Canonical target discovery and resolution revalidation supply the surviving
original references. The continuous-effect state owner commits the same
layer-six and layer-seven components over that resolution-locked set before
stabilization. No new semantic registry or per-card runtime path is added.
Independently assured scalar amount composition can project and resolve stat
result slots without changing target cardinality or keyword semantics.

## Alternatives

Repeating a single-target effect by physical reference could reach a new
incarnation after a response. Treating up-to wording as mandatory would hide
legal zero-target choices. Expanding the grammar without carrying its count
through offers, accepted commands, and revalidation would give false support.

## Consequences

Fixed stat and represented keyword grants support exact, up-to, and one-or-two
target ranges within the existing bound. Mixed target roles, chosen abilities,
base-setting and type conversion, and unsupported sibling instructions remain
residuals. New entries and returning incarnations do not inherit the effect.

## Verification

Original spells cover zero, one, and two selections, duplicate and principal
rejection, partial target invalidation, stat and keyword results, and expiry.
Original entry-trigger choices preserve pending save/load and pre-action
replay. Typed scalar composition and omission mutations retain explicit
positive and negative evidence through the same runtime owners.
