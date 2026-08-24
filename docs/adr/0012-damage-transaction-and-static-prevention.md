---
title: "ADR 0012: damage transaction and static prevention ownership"
status: "ADR"
authoritative_source: "this decision record and platform/architecture-policy.json"
verified: "2026-08-02"
audience: "rules, semantics, replay, and architecture contributors"
maintenance: "hand-maintained"
adr_id: "0012"
decision_status: "accepted"
date: "2026-08-02"
---

# ADR 0012: damage transaction and static prevention ownership

## Context

Combat, semantic effects, and mana abilities applied damage through separate
mutation paths. Protection was special-cased during combat, noncombat damage
did not share final-event dispatch, and live source-pinned replacement or
prevention effects could not suspend an affected-subject choice before results.
This made CR 120.4b/120.4d and the represented CR 614/615/616 interactions
neither composable nor exactly replayable.

## Decision

`damage.py` owns an immutable proposal/preparation/commit transaction for all
represented damage producers. It snapshots source and recipient facts, runs
the shared replacement-event batch before mutation, validates every
simultaneous result, commits the represented base result, and publishes the
normalized final damage event used by triggers and audit logs.

`semantic_runtime/damage_replacements.py` owns strict source-pinned descriptor
validation and lowering for fixed quantity replacement and fixed static
prevention. It remains pure. Protection contributes an immutable prevention
effect at preparation. Its canonical source snapshot includes the represented
type, subtype, supertype, color, and mana-value facts needed by compiled fixed
Protection qualities, including last-known information after source departure.
`replacement_decisions.py` owns seat-scoped combat and
semantic suspension; Game Record v3 remains unchanged.

Rules keywords such as toxic and lifelink, and transport fields such as reason,
can collide with printed card names in the repository specificity scanner.
Their reviewed occurrences in the generic damage owner are architectural rules
vocabulary, not card dispatch. The ADR-bound specificity refresh records only
those exact sites; printed-card branching remains prohibited.

## Alternatives

- Keep protection and replacement logic in combat. Rejected because noncombat
  and mana-result damage must use the same ordering and trigger boundary.
- Let runtime components mutate life or permanents. Rejected because that
  would create a second state authority outside the transaction owner.
- Mark broad CR 614/615/616 capabilities trusted. Rejected because persistent
  shields, redirection, result replacement, and several producer
  continuations remain incomplete.
- Silently choose replacement order during mana payment. Rejected because the
  affected player owns that decision; the action instead fails before damage
  until the enclosing continuation is resumable.

## Consequences

- Represented damage has one atomic precommit and final-event boundary.
- Combat and semantic choices are projected to one seat and replay exactly.
- Source-pinned static replacement/prevention can expand through data without
  printed-name engine conditionals.
- Unsupported result and continuation families fail before damage rather than
  producing a plausible but wrong result.
- The central engine loses direct damage mutation, while orchestration and
  compatibility adapters remain during incremental extraction.

## Removal condition

The compatibility adapter may disappear when every caller uses the typed port.
The mana-choice fail-closed boundary may be removed only after casting,
activation, and mana payment carry resumable typed frames that cannot replay
already-paid costs or produced mana.
