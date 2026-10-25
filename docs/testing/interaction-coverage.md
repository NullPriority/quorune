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
Runtime-admission witnesses exercise the actual trust boundary, including a
pause before unsupported resolution. Preserve the source's normal zone, legal
timing, and sufficient payment so the witness isolates the unrepresented operation.
A nearby accepted control demonstrates that the supported owner remains usable.

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

Fixed public characteristic settings have explicit composition witnesses with
both Aura targeting and Equip recipient changes. Whole-hand discard has a
player-target revalidation witness in which the target concedes before the
spell resolves, preserving every surviving hand and exact replay. Original
carriers combining quantity counters, public-quantity mana or whole-hand
discard with unresolved prevention, replacement, Saga or choice siblings have
separate whole-CardProgram admission witnesses. Those declarations establish
fail-closed rejection; they do not claim the unresolved siblings are playable.

Typed Aura restrictions and defender-permission grants have an original-card
cast, illegal-target, actual-removal and replay witness. Player Shroud also
composes with source-maintenance payment and canonical sacrifice redirection:
declining the private payment removes the protection source and restores legal
targeting. Fixed resolution mana beside unrepresented replacement siblings
uses original complete-card admission witnesses; those combinations remain
rejected even when the mana prefix is exact.

Conditional land entry also composes with targeted owner-library results through
original-card land plays. The entry threshold determines whether an untapped-entry
trigger exists; the chosen graveyard incarnation is then revalidated independently
after a response moves it. Residual regeneration, targeting, and multiple-damage
siblings retain separate whole-program admission rejection witnesses.

Source-departure exile has actual Aura attachment and state-based departure
witnesses, a target-incarnation revalidation witness, and a constructed Equip
witness that distinguishes reattachment from source departure. Restricted Equip
also has a constructed live turn-condition witness. Original Equipment carriers
with unresolved copy or attack-alone siblings remain rejected at whole-program
admission. Random discard revalidates a departed player before sampling any
surviving hand. Controller sacrifice can select a Hexproof permanent because
that choice does not target it.
