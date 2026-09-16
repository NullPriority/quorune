---
title: "ADR 0099: typed public declaration conditions"
status: "ADR"
authoritative_source: "shared public-state conditions, declaration fragments, attachment components, and combat declaration solver"
verified: "2026-09-16"
audience: "rules, compiler, combat, activation, replay, and architecture maintainers"
maintenance: "hand-maintained"
adr_id: "0099"
decision_status: "accepted"
date: "2026-09-16"
---

# ADR 0099: typed public declaration conditions

## Context

The declaration solver already consumes typed costs, restrictions, and
requirements, but many Oracle lines remained residual because their
restrictions depended on current public facts. Those facts overlapped the
existing fixed public-state characteristic owner: controller hand and
graveyard counts, current-turn draws and casts, source counters, attachment
state, and cycle-safe public object quantities. Reimplementing them inside the
combat solver would create a second condition registry and risk disagreeing
offer and commit legality.

Several common Aura restraints also combine a declaration prohibition with an
activation prohibition. Treating the latter as Oracle text or a separate
family-specific lookup would bypass the shared layer-6 ability-presence query.
Source-controller wording adds another distinction: a restriction granted to
the enchanted creature still uses the Aura's current controller as “you.”

## Decision

`DeclarationRestrictionTemplate` may carry the existing
`FixedPublicStateConditionSpec`. The declaration owner evaluates that condition
through the same immutable snapshot and cycle-safe quantity resolver used by
static characteristic components. Source power and toughness comparisons use
current effective characteristics at declaration time; open characteristic
counts and cross-stat expressions remain excluded.

The compiler admits a closed public condition grammar for fixed hand,
graveyard, battlefield, counter, attachment, draw, cast, and current-stat
facts. It preserves historical template identities when an older exact parser
already owns the wording. Recipient-relative restrictions remain anchored to
their originating static source, so control changes update “you” without
retargeting the affected creature.

`ActivationProhibitionSpec` is a typed layer-6 ability fragment with either
all-ability or nonmana scope. Exact attached restraints add it through the
existing attached-characteristic component. Activation advertisement and
commit both query the current effective fragment set. Detachment, phasing,
source departure, and ability removal therefore end the prohibition through
the same applicability boundary as declaration additions and removals.

## Alternatives

- Add combat-local condition enums. Rejected because the public-state owner
  already defines the authoritative facts and cycle boundary.
- Parse restriction prose during attack, block, or activation queries.
  Rejected because runtime Oracle parsing is prohibited and would split offer
  from commit authority.
- Attach every source-relative restriction to the affected creature. Rejected
  because Aura control changes would change the wrong controller relation.
- Treat activation restraint as an unrelated global runtime component.
  Rejected because current attachment and ability applicability are layer-6
  facts already owned by the effective fragment query.
- Admit temporary, chosen, payment, and open dynamic-count forms together.
  Rejected because they require distinct resolution, choice, cost, or
  characteristic boundaries.

## Consequences

The represented declaration cohort reuses one public condition snapshot, one
combat constraint solver, and one current static-component query. Offer and
commit legality recompute from live hand counts, graveyards, counters,
attachments, history, control, and effective characteristics. No card identity,
runtime prose, parallel declaration engine, or family-specific ability check is
introduced.

Nonmana declaration costs, chosen or named values, temporary mass
restrictions, crew and transform riders, ignore-this-effect payments,
unsupported previous-turn facts, and open dynamic characteristic counts remain
material residuals. Historical declaration predicates lacking the additive
counter-presence field remain replay-readable with their original defaults.

## Removal condition

Retire this boundary only if a successor preserves the shared public-state
snapshot, cycle-safe characteristic evaluation, source-relative controller
identity, current layer-6 applicability, offer/commit parity, rollback,
projection, persistence, and exact replay without runtime Oracle parsing or a
second combat or activation authority.
