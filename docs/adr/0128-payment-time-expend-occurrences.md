---
title: "ADR 0128: payment-time Expend occurrences"
status: "ADR"
authoritative_source: "typed spell-mana payment event, casting commit and bounded turn history"
verified: "2026-10-27"
audience: "rules, casting, compiler and replay contributors"
maintenance: "hand-maintained"
adr_id: "0128"
decision_status: "accepted"
date: "2026-10-27"
---

# ADR 0128: payment-time Expend occurrences

## Context

CR 700.14 depends on a crossing of actual mana spent to cast spells during the
current turn. A cast's printed mana value, its payment after reduction, and
mana spent on activated abilities can differ. The event also precedes later
casting costs that remove a permanent supplying the triggered ability.

## Decision

Use one immutable payment event with player and before/after totals. Record
positive payments in the existing bounded current-turn journal under an
explicit `spell_mana_history_version` marker. New games enable the marker;
reset preserves it and clears the facts. Historical absence retains its old
payload and disables new recording. The Game Record manifest binds the marker
to the initial checkpoint.

Immediately after the canonical spell mana payer returns, capture matching
occurrences through the existing semantic trigger owner. Keep them in a local
batch and append them to the cast's existing APNAP placement batch after the
remaining costs and stack commit. The outer cast transaction rolls back the
journal and captured occurrences together. Current controller, source
incarnation and ability applicability are read at payment time.

The compiler stores separate player, strict-before and inclusive-after
predicates for a fixed positive threshold. Source-entry pairs retain both
subscriptions and one shared body. Closed source-result pronouns in this
composition delegate to the existing source-incarnation result owners.
Runtime binding infers the event capability directly from single or paired
subscriptions, independently of descriptive coverage markers.

## Alternatives

- Recomputing from display logs confuses diagnostics with authoritative state.
- Using printed mana value counts reductions, alternative costs and free
  spells incorrectly.
- Checking only the total after payment retriggers at later payments.
- Discovering only after additional costs loses already triggered sources.
- A separate persistent counter duplicates the existing bounded journal and
  introduces another reset and compatibility contract.

## Consequences

Native witnesses distinguish earlier spending, an entering source's own
payment, opponent spending, activation payments, free/reduced spells,
same-cost source sacrifice, rollback, target authority, turn reset and replay.
Unrepresented result bodies remain residual. The change adds no Oracle parsing
to runtime, additional card registry, or independent APNAP scheduler.
