---
title: "Interaction coverage"
status: "current"
authoritative_source: "platform/reusable-piece-interaction-evidence.json, capability evidence, mechanic contracts, conformance records, semantic programs, and tests"
verified: "2026-08-10"
audience: "rules and test contributors"
maintenance: "hand-maintained"
---

# Interaction coverage

Rule-by-rule and card-by-card tests are necessary but cannot establish the
composition of effects. Interaction assurance tracks reusable capabilities,
applicable pairs, high-risk three-way combinations, official rulings, and
discrepancies found by differential or mutation testing.

For each rules slice, record:

- the `interaction` evidence class, exact test ID, and exact piece pair or
  higher-order tuple in `platform/reusable-piece-interaction-evidence.json`;
- participating capability or temporary mechanic-contract IDs;
- legal and illegal orderings, targets, costs, and visibility contexts;
- replacement/prevention competition and trigger/APNAP ordering;
- zone changes, last-known information, copy/control changes, and state-based
  action boundaries that can alter the result;
- whether the case has direct, property, mutation, replay, and privacy evidence;
- unresolved semantic/compiler dependencies.

Coverage must be derived into machine-readable reports. A green unit test for
one card does not promote its untested interactions. Two pieces citing the same
general contract test are not covered unless an explicit declaration says that
test asserts their interaction. Higher-order declarations project only the
pairs they actually name. An exact compiler node does not promote an untrusted
runtime dependency. The versioned capability registry is the authoritative
fine-grained trust graph for migrated slices; current mechanic contracts and
conformance cases remain migration inputs where fine-grained capability
mappings do not yet exist.

Zero uncovered high-risk debt is always relative to the currently modeled,
applicable interaction set. Reports distinguish successful supported
composition, demonstrated fail-closed rejection of an explicitly unsupported
composition, and unknown or unmodeled composition. A rejection witness must not
be reported as successful playability of the pair.

Every semantic scope expansion reviews newly reachable ambient interactions,
even when no capability ID changes. An unchanged denominator is acceptable only
with a reason grounded in existing evidence. Select stateful and higher-order
cases by reachable behavior, failure history, shared authority, and risk rather
than attempting every pair or three-way combination. Relevant official rulings
inform the expected result; their presence alone is not conformance evidence.

The matrix includes both printed-card co-occurrence and cross-card composition.
`platform/reusable-piece-policy.json` declares bounded ambient high-risk piece
pairs whose interaction is reachable even when no single card prints both
pieces. An explicit evidence declaration also makes its exact pair visible.
Each generated row records whether it came from corpus co-occurrence, an
ambient high-risk declaration, explicit interaction evidence, or a combination.
Adding an ambient pair without evidence therefore creates an uncovered
high-risk row; evidence can cover it only by naming a registered test and the
exact participating pieces.

The current keyword-counter assurance grid treats the boundary between
canonical counter placement, layer-6 characteristic projection, and each
independent executable consumer as ambient high risk. It covers replacement
ordering, Flying, Vigilance, Double strike, Lifelink, Deathtouch, Trample,
Menace, Indestructible, and ordinary permanent Hexproof only through their
named capability pairs. Other entries in the CR 122.1b vocabulary remain
unknown until an exact consumer interaction is declared and certified.
