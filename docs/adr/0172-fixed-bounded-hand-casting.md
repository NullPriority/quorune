---
title: "ADR 0172: Fixed bounded hand casting"
status: "ADR"
authoritative_source: "CR 107.3b, 601.2, 601.3e and 608.2g"
verified: "2026-11-16"
audience: "compiler, casting, privacy and replay contributors"
maintenance: "hand-maintained"
adr_id: "0172"
decision_status: "accepted"
date: "2026-11-16"
---

# ADR 0172: Fixed bounded hand casting

## Context

An instruction may allow one optional spell from the current hand during its
resolution. Its fixed mana-value ceiling constrains the proposed spell, while
canonical casting still determines costs and targets. Earlier draws or returns
can add eligible cards; no public reveal or lasting permission is implied.

## Decision

Use a closed `BoundedHandCastSpec` in the existing object-choice operation and
an immutable `ResolveBoundedHandCastIntent`. Delegate face/method views, costs,
restrictions and targets to the same casting proposal and payment owners used
by actual accepted commands. The free alternative price excludes another
alternative cost, fixes X to zero and retains additional costs and increases.

Use `ManaValueLimitSpec` on the proposed spell's effective value. The chosen
split half or modal face is considered separately; Fuse combines both halves.
The pinned version of CR 601.3e supersedes the dated combined-card eligibility
interpretation retained in older Expertise rulings. Record that source conflict
in the independent behavior contract instead of repeating the older result.

Keep the offer actor-private and bind option IDs to logical incarnations and
canonical prices. Revalidate the permission, original stack/controller and
option fingerprint before delegating a selected hand cast. Reuse existing
replacement continuation and replay validation with an explicit new intent
kind; historical captured-draw, exile and graveyard kinds remain unchanged.

## Alternatives

- Restricting selection to captured draws would discard valid old hand cards.
- Looking only at a hand card's front or combined value would reject legal
  proposed faces and split halves.
- A persistent cast permission would extend the instruction beyond resolution.
- A second casting or payment implementation would split offer and command
  legality and cost replacement authority.

## Consequences

The parent remains on the stack during casting and continues afterward without
priority or mid-resolution state-based actions. Ordinary trigger placement,
parent target revalidation, child resolution and rollback remain canonical.
Variable bounds, typed restrictions, other seats/zones, multiple casts and
paid alternatives need their own represented instructions.
