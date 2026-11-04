---
title: "ADR 0161: ordinary attacking-token results"
status: "ADR"
authoritative_source: "fixed token compiler, semantic choice registry, token transaction and Battle Cry resolution owner"
verified: "2026-11-03"
audience: "rules, compiler, replay and architecture contributors"
maintenance: "hand-maintained"
adr_id: "0161"
decision_status: "accepted"
date: "2026-11-03"
---

# ADR 0161: ordinary attacking-token results

## Context

CR 508.4 lets an entering attacking creature's controller choose its defending
recipient when the effect does not specify one. Creating such a creature does
not declare an attacker. Existing Mobilize and Myriad choices already implement
public destination selection and replacement-aware token entry. Mobilize also
fixes a red Warrior definition and adds an end-step sacrifice; those contracts
cannot describe ordinary printed token results.

Battle Cry's earlier bounded owner explicitly excluded creatures put onto the
battlefield attacking. It sealed the declared affected set and rejected an
unrepresented new attacker. CR 702.91a, 508.4 and 608.2h require the current
other attacking creatures when the trigger resolves. Ordinary attacking-token
results make that integration necessary.

## Decision

Add only `choose_fixed_attacking_token_destinations` to the reviewed operation
inventory. Its closed version-one descriptor contains an independently validated
fixed creature definition, quantity and printed tapped state. It chooses one
public destination per replacement-expanded token before the existing token
transaction commits. Unknown definitions, recipient wording, copy recipes and
unrepresented delayed riders remain residual.

Share public recipient selection, preview validation and intent construction
with Mobilize. Keep Mobilize and Myriad operations, saved continuation shapes
and lifecycle effects. The new result uses no unprinted sacrifice. The choice
query supplies an immutable fact about whether the controller is an attacking
player; otherwise the printed tokens enter without joining combat under
CR 506.3b. No new engine mutation owner or registry is introduced.

Pin `battle_cry_resolution_version: 1` for new games. That policy selects current
other attacking logical incarnations when Battle Cry resolves, then uses the
existing identity-pinned layer-7c result. The original source incarnation is
excluded; an unrelated later incarnation is a different object. A resolved
continuous effect does not acquire later attackers.

Historical marker absence preserves the earlier declared-set and rejection
contract and remains absent on serialization. Checkpoint and manifest markers
must agree. Unknown or boolean versions reject; historical capability and
runtime provenance checks remain independent.

## Alternatives

Extending Mobilize's existing descriptor would mix an ordinary printed result
with its fixed Warrior definition and mandatory sacrifice. Duplicating the
destination and token-entry logic would create another authority for the same
rules. The separate closed operation shares those owners and preserves the
existing saved formats.

Keeping Battle Cry's declared recipient set would reject the newly represented
legal ordering. Applying the new set to historical records would reinterpret
their behavior. The explicit execution marker distinguishes those contracts.

## Consequences

The exact baseline review adds one typed choice operation. Existing engine
methods, mutation identities, module/function size ceilings and identity-flow
limits do not increase. The shared destination extraction reduces the older
choice coordinator and does not duplicate token entry or APNAP ownership.

Focused evidence covers independently expanded destination maps, malformed
definitions, stale continuations, rollback, native Crew and combat consequences,
entry-before-Battle-Cry timing, private projection and replay. Existing Mobilize
evidence remains the shared replacement and player/planeswalker/battle witness.
Small owner tests distinguish current recipient selection from historical
rejection and kill reuse of the declared set.

Remove this inventory allowance when the existing capability-bound choice
registry directly governs the operation inventory. No size or direct-write
exception is introduced for that future generalization.
